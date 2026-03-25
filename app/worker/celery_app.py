from celery import Celery
from core.config import settings
from core.idp_logger import log_event, timed_stage, build_simplified_json
from core.minio_client import get_minio_client, upload_file_to_minio
from sqlalchemy import create_engine, text
import json
import os
import traceback
import uuid

# Engine Components
from engine.vision_optimized import (
    extract_markdown_from_minio_sync as extract_markdown_from_minio,
    extract_visual_analysis_sync
)
from engine.agent import extract_form_data
from engine.mapper import get_json_schema, map_results_to_json, extract_fields_from_schema
from engine.smart_router import get_best_worker, WorkerDestination, is_cloud_provider
from engine.hardware_detector import detect_hardware, apply_thread_limits

# Aplicar límites de threads ANTES de importar torch/numpy/docling
_hw = detect_hardware()
apply_thread_limits(_hw)

# Create celery application
celery_app = Celery(
    "idp_worker",
    broker=settings.valkey_url,
    backend=settings.valkey_url
)

# ── Umbral de recuperación de tareas huérfanas (en minutos) ──────────────────
ORPHAN_RECOVERY_MINUTES = 5

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='America/Mexico_City',
    enable_utc=True,
    worker_concurrency=1,
    # ── Beat: tarea de recuperación automática cada 5 minutos ──────────────
    beat_schedule={
        'recover-orphaned-tasks': {
            'task': 'recover_orphaned_tasks',
            'schedule': ORPHAN_RECOVERY_MINUTES * 60,  # segundos
        },
    },
)

# Sync engine for Celery
sync_database_url = settings.database_url.replace("postgresql+asyncpg", "postgresql")
db_engine = create_engine(sync_database_url)

# Iniciar RunPod Idle Watcher si está habilitado
if settings.runpod_enabled:
    _pod_ids = [p for p in [settings.runpod_pod_docling_id, settings.runpod_pod_llm_id] if p]
    if _pod_ids:
        from engine.runpod_manager import start_idle_watcher
        start_idle_watcher(_pod_ids, idle_timeout=settings.runpod_idle_timeout)

import time
from functools import wraps

def monitor_performance(func):
    """Decorador para registrar métricas de rendimiento en la BD."""
    @wraps(func)
    def wrapper(task_id, *args, **kwargs):
        start_t = time.time()
        # Registrar inicio
        with db_engine.begin() as conn:
            conn.execute(
                text("UPDATE idp_smart.document_extractions SET started_at = NOW(), llm_provider = :p WHERE task_id = :tid"),
                {"p": settings.llm_provider, "tid": uuid.UUID(str(task_id))}
            )
        try:
            return func(task_id, *args, **kwargs)
        finally:
            elapsed = time.time() - start_t
            with db_engine.begin() as conn:
                conn.execute(
                    text("UPDATE idp_smart.document_extractions SET total_duration_s = :e WHERE task_id = :tid"),
                    {"e": round(elapsed, 2), "tid": uuid.UUID(str(task_id))}
                )
    return wrapper

# Etapas definidas del proceso (usadas para cálculo de progreso en frontend)
STAGES = ["INICIO", "VISION", "QWEN_VISION", "SCHEMA_LOAD", "AGENT", "MAPPER", "SIMPLIFY", "DB_SAVE", "ERROR"]

def log_benchmark(
    task_id,
    expediente_id,
    gpu,
    d_time,
    v_time,
    r_time,
    cost_usd: float = 0.0,
    oom_detected: bool = False,
    processing_unit: str = "CPU",
):
    """
    Inserta métricas de rendimiento en hardware_benchmarks.
    Incluye: costo USD (cloud), OOM flag y etiqueta processing_unit (CPU | GPU:<modelo>).
    """
    try:
        total = d_time + v_time + r_time
        with db_engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO idp_smart.hardware_benchmarks
                    (task_id, gpu_model, docling_time, vision_time, reasoning_time,
                     total_time, cost_usd, oom_detected, processing_unit)
                    VALUES (:tid, :gpu, :dt, :vt, :rt, :tt, :cost, :oom, :pu)
                """),
                {
                    "tid":  uuid.UUID(str(task_id)),
                    "gpu":  gpu,
                    "dt":   round(d_time, 2),
                    "vt":   round(v_time, 2),
                    "rt":   round(r_time, 2),
                    "tt":   round(total, 2),
                    "cost": round(cost_usd, 6),
                    "oom":  oom_detected,
                    "pu":   processing_unit,
                }
            )
            cost_str = f" | ${cost_usd:.5f}" if cost_usd else ""
            oom_str  = " [OOM]" if oom_detected else ""
            print(
                f"BENCHMARK [{processing_unit}]: {gpu} | "
                f"{total:.2f}s (D:{d_time:.1f} V:{v_time:.1f} R:{r_time:.1f})"
                f"{cost_str}{oom_str}"
            )
    except Exception as e:
        print(f"Error guardando benchmark: {e}")


def _set_stage(task_id: str, stage: str, status: str = None):
    """Actualiza stage_current y status en la BD para que el frontend vea el avance."""
    try:
        with db_engine.begin() as conn:
            if status:
                conn.execute(
                    text("""
                        UPDATE idp_smart.document_extractions
                        SET stage_current = :stage, status = :status, updated_at = NOW()
                        WHERE task_id = :task_id
                    """),
                    {"stage": stage, "status": status, "task_id": uuid.UUID(str(task_id))},
                )
            else:
                conn.execute(
                    text("""
                        UPDATE idp_smart.document_extractions
                        SET stage_current = :stage, updated_at = NOW(), llm_provider = :llm_provider
                        WHERE task_id = :task_id
                    """),
                    {"stage": stage, "task_id": uuid.UUID(str(task_id)), "llm_provider": settings.llm_provider},
                )
    except Exception as exc:
        log_event(db_engine, task_id, "SYSTEM", f"No se pudo actualizar stage_current a {stage}: {exc}", level="WARNING")


@celery_app.task(name="process_doc")
@monitor_performance
def process_doc(task_id: str, json_minio_object: str, pdf_minio_object: str, skip_vision: bool = False):
    """
    Pipeline de extracción semántica de documentos.
    Limpia automáticamente prefijos de bucket duplicados.
    """
    # Limpieza de rutas: Si vienen con el nombre del bucket al inicio, lo quitamos
    bucket_prefix = f"{settings.minio_bucket}/"
    if json_minio_object.startswith(bucket_prefix):
        json_minio_object = json_minio_object[len(bucket_prefix):]
    if pdf_minio_object.startswith(bucket_prefix):
        pdf_minio_object = pdf_minio_object[len(bucket_prefix):]

    minio_client = get_minio_client()
    doc_markdown = None

    try:
        # ── INICIO ───────────────────────────────────────────────────────────────
        with db_engine.begin() as conn:
            conn.execute(
                text("UPDATE idp_smart.document_extractions SET started_at = NOW(), stage_current = 'INICIO', llm_provider = :llm_provider WHERE task_id = :tid"),
                {"tid": uuid.UUID(str(task_id)), "llm_provider": settings.llm_provider}
            )
        
        log_event(db_engine, task_id, "INICIO",
                  f"Tarea iniciada — doc: {pdf_minio_object} | form: {json_minio_object}",
                  detail={"pdf": pdf_minio_object, "form": json_minio_object, "skip_vision": skip_vision})

        # Verificar si ya tenemos el markdown en la base de datos para retomar o rehusar
        with db_engine.connect() as conn:
            result = conn.execute(
                text("SELECT markdown_minio_path, parent_task_id FROM idp_smart.document_extractions WHERE task_id = :tid"),
                {"tid": uuid.UUID(str(task_id))}
            ).fetchone()
            
            target_md_path = None
            if result:
                target_md_path = result[0]
                parent_tid = result[1]
                
                # Si no hay markdown en esta tarea pero hay una tarea padre, buscarlo allá
                if not target_md_path and parent_tid:
                    res_parent = conn.execute(
                        text("SELECT markdown_minio_path FROM idp_smart.document_extractions WHERE task_id = :tid"),
                        {"tid": uuid.UUID(str(parent_tid)) if parent_tid else None}
                    ).fetchone()
                    if res_parent:
                        target_md_path = res_parent[0]
                        log_event(db_engine, task_id, "INICIO", f"Reutilizando Markdown de la tarea padre: {parent_tid}")

            if target_md_path and skip_vision:
                log_event(db_engine, task_id, "INICIO", "Markdown encontrado, intentando recuperar de MinIO.")
                try:
                    obj_path = target_md_path.split("idp-documents/")[-1]
                    res_md = minio_client.get_object("idp-documents", obj_path)
                    doc_markdown = res_md.read().decode("utf-8")
                    log_event(db_engine, task_id, "INICIO", f"Markdown recuperado exitosamente ({len(doc_markdown)} chars).")
                except Exception as e_md:
                    log_event(db_engine, task_id, "INICIO", f"No se pudo recuperar markdown existente: {e_md}. Re-procesando VISION.", level="WARNING")
                    skip_vision = False
        # ── VISION ───────────────────────────────────────────────────────────────
        additional_mds = []
        
        # Obtener información de documentos adicionales de la BD
        with db_engine.connect() as conn:
            extra_result = conn.execute(
                text("SELECT additional_docs FROM idp_smart.document_extractions WHERE task_id = :tid"),
                {"tid": task_id}
            ).fetchone()
            additional_paths = extra_result[0] if extra_result and extra_result[0] else []

        # ── PASO 1: EXTRACCIÓN (Docling) ────────────────────────────────────────
        docling_start = time.time()
        docling_duration = 0.0
        gpu_model = "CPU"
        
        if not skip_vision:
            _set_stage(task_id, "VISION")
            with timed_stage(db_engine, task_id, "VISION", "Extracción Markdown — Documento Principal"):
                log_event(db_engine, task_id, "VISION", "[PASO 1] Docling: Extracción estructural")
                # La nueva firma retorna (markdown, pages, gpu_model)
                doc_markdown, p_count, gpu_model = extract_markdown_from_minio(pdf_minio_object)
                docling_duration = time.time() - docling_start
                
                # Actualizar page_count y gpu_model en BD
                with db_engine.begin() as conn:
                    conn.execute(
                        text("UPDATE idp_smart.document_extractions SET page_count = :pc, gpu_model = :gpu WHERE task_id = :tid"),
                        {"pc": p_count, "gpu": gpu_model, "tid": uuid.UUID(str(task_id))},
                    )
                if not doc_markdown:
                    doc_markdown = "# Documento Principal\nContenido no extraído."
        else:
            doc_markdown = "" # Se recuperará si existía o se asume vacío si skip
            log_event(db_engine, task_id, "VISION", "Saltando etapa VISION (Step 1).")

        # ── PASO 2: VISIÓN (Qwen2-VL) ───────────────────────────────────────────
        vision_start = time.time()
        vision_duration = 0.0
        visual_analysis = ""
        
        if settings.llm_provider == "runpod" and not skip_vision:
            _set_stage(task_id, "QWEN_VISION")
            with timed_stage(db_engine, task_id, "QWEN_VISION", "Análisis Visual de Zonas Críticas (Qwen2-VL)"):
                log_event(db_engine, task_id, "QWEN_VISION", "[PASO 2] Capturando y analizando firmas/sellos")
                
                # Necesitamos el path local temporal (reutilizando lógica o bajando de nuevo)
                import tempfile
                import os
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    minio_client.fget_object(settings.minio_bucket, pdf_minio_object, tmp.name)
                    visual_analysis = extract_visual_analysis_sync(tmp.name)
                    os.remove(tmp.name)
                
                vision_duration = time.time() - vision_start
                if visual_analysis:
                    doc_markdown = (doc_markdown or "") + "\n" + visual_analysis
                    log_event(db_engine, task_id, "QWEN_VISION", "Análisis visual fusionado en el contexto.")

        # Procesar Documentos Adicionales (Legacy/Adendas)
        if additional_paths:
            with timed_stage(db_engine, task_id, "VISION", f"Extrayendo {len(additional_paths)} documentos adicionales"):
                for i, path in enumerate(additional_paths):
                    try:
                        obj_name = path.split("idp-documents/")[-1]
                        add_md, _, _ = extract_markdown_from_minio(obj_name)
                        if add_md:
                            header = f"\n\n--- DOCUMENTO ADICIONAL {i+1} ---\n"
                            additional_mds.append(header + add_md)
                    except Exception as e_add:
                        log_event(db_engine, task_id, "VISION", f"Error en doc adicional {i+1}: {e_add}", level="WARNING")
        
        if additional_mds:
            doc_markdown = (doc_markdown or "") + "".join(additional_mds)
            log_event(db_engine, task_id, "VISION", f"Contexto total consolidado: {len(doc_markdown)} caracteres.")

        # Guardar el Markdown consolidado en MinIO (si es nuevo o cambió)
        if not skip_vision or additional_mds:
            try:
                md_bytes = doc_markdown.encode("utf-8")
                md_object_name = f"{task_id}/extracted_markdown_full.md"
                markdown_path = upload_file_to_minio(minio_client, md_object_name, md_bytes, "text/markdown")
                log_event(db_engine, task_id, "VISION",
                          f"Markdown persistido en MinIO: {markdown_path}",
                          detail={"minio_path": markdown_path})
                # Actualizar ruta en BD
                with db_engine.begin() as conn:
                    conn.execute(
                        text("UPDATE idp_smart.document_extractions SET markdown_minio_path = :path WHERE task_id = :tid"),
                        {"path": markdown_path, "tid": task_id},
                    )
            except Exception as exc:
                log_event(db_engine, task_id, "VISION", f"No se pudo persistir markdown consolidado: {exc}", level="WARNING")
        else:
            log_event(db_engine, task_id, "VISION", "Saltando etapa VISION (Markdown ya disponible o solicitado saltar).")

        # ── SCHEMA_LOAD ──────────────────────────────────────────────────────────
        _set_stage(task_id, "SCHEMA_LOAD")
        schema = None
        flat_fields = []
        with timed_stage(db_engine, task_id, "SCHEMA_LOAD", "Carga de esquema JSON desde MinIO"):
            schema = get_json_schema("idp-documents", json_minio_object)
            flat_fields = extract_fields_from_schema(schema)
            field_count = len(flat_fields)
            log_event(db_engine, task_id, "SCHEMA_LOAD",
                      f"Esquema JSON cargado con {field_count} campo(s) a extraer.",
                      detail={"field_count": field_count})

        # ── PASO 3: RAZONAMIENTO (Multimodal / Gemini / Granite) ────────────────
        _set_stage(task_id, "AGENT")
        extracted_key_val = None
        ai_start = time.time()
        inference_cost_usd = 0.0

        image_paths = []
        if settings.llm_provider == "google" and not skip_vision:
            try:
                import fitz
                import tempfile
                import os

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                    minio_client.fget_object(settings.minio_bucket, pdf_minio_object, tmp_pdf.name)
                    doc = fitz.open(tmp_pdf.name)

                    indices = [0]
                    if len(doc) > 1: indices.append(len(doc)-1)

                    for idx in indices:
                        page = doc.load_page(idx)
                        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                        img_path = f"/tmp/{task_id}_page_{idx}.png"
                        pix.save(img_path)
                        image_paths.append(img_path)

                    doc.close()
                    os.remove(tmp_pdf.name)

                gpu_model = "Google-Gemini"
                log_event(db_engine, task_id, "AGENT", f"Pipeline Multimodal Gemini ({len(image_paths)} imagenes)")
            except Exception as e_img:
                log_event(db_engine, task_id, "AGENT", f"Error imagenes Gemini: {e_img}", level="WARNING")

        with timed_stage(db_engine, task_id, "AGENT",
                         f"Fusion Legal: Razonamiento {settings.llm_provider.upper()} + Multimodalidad"):
            try:
                extracted_key_val = extract_form_data(
                    doc_markdown,
                    schema,
                    visual_analysis=visual_analysis,
                    image_paths=image_paths
                )
            except Exception as agent_err:
                # -- CLOUD FALLBACK AUTOMATICO ---------------------------------------
                cloud_fallback = str(getattr(settings, "enable_cloud_fallback", "false")).lower() == "true"
                is_local = settings.llm_provider in ("local", "runpod")
                if cloud_fallback and is_local:
                    import os as _os
                    fallback_provider = _os.environ.get("CLOUD_FALLBACK_PROVIDER", "gemini")
                    log_event(
                        db_engine, task_id, "AGENT",
                        f"Fallo motor local ({agent_err}). Activando CLOUD FALLBACK: {fallback_provider.upper()}",
                        level="WARNING"
                    )
                    from engine.llm_factory import get_llm_provider
                    import re as _re
                    fallback_llm = get_llm_provider(fallback_provider)
                    raw_prompt = f"{doc_markdown}\n\n---\nExtrae los campos del formulario en JSON."
                    text_result, inference_cost_usd = fallback_llm.invoke_with_cost(raw_prompt)
                    gpu_model = f"CLOUD-{fallback_provider.upper()}"
                    json_match = _re.search(r"\{.*\}", text_result, _re.DOTALL)
                    if json_match:
                        try:
                            extracted_key_val = json.loads(json_match.group())
                        except json.JSONDecodeError:
                            extracted_key_val = {}
                    else:
                        extracted_key_val = {}
                    log_event(
                        db_engine, task_id, "AGENT",
                        f"Fallback cloud completado. Costo: ${inference_cost_usd:.5f} USD",
                        detail={"provider": fallback_provider, "cost_usd": inference_cost_usd}
                    )
                else:
                    raise

            for p in image_paths:
                if os.path.exists(p): os.remove(p)

            filled_count = sum(1 for v in extracted_key_val.values() if v) if extracted_key_val else 0
            total_count = len(flat_fields)
            log_event(db_engine, task_id, "AGENT",
                      f"Razonamiento completado (Llenos: {filled_count}/{total_count}).",
                      detail={"filled": filled_count, "total": total_count})
        ai_duration = time.time() - ai_start

        # Registrar Benchmark (con costo cloud si aplica)
        log_benchmark(
            task_id, pdf_minio_object, gpu_model,
            docling_duration, vision_duration, ai_duration,
            cost_usd=inference_cost_usd
        )

        # ── MAPPER ────────────────────────────────────────────────────────────────
        _set_stage(task_id, "MAPPER")
        final_json = None
        with timed_stage(db_engine, task_id, "MAPPER", "Mapeo de UUIDs y reconstrucción del JSON"):
            final_json = map_results_to_json(schema, extracted_key_val)

        # ── SIMPLIFY ──────────────────────────────────────────────────────────────
        _set_stage(task_id, "SIMPLIFY")
        simplified = {}
        with timed_stage(db_engine, task_id, "SIMPLIFY", "Generación de JSON simplificado {label: value}"):
            simplified = build_simplified_json(final_json)
            log_event(db_engine, task_id, "SIMPLIFY",
                      f"JSON simplificado generado con {len(simplified)} campo(s).",
                      detail={"fields": list(simplified.keys())[:20]})

        # ── DB_SAVE ───────────────────────────────────────────────────────────────
        _set_stage(task_id, "DB_SAVE")
        with timed_stage(db_engine, task_id, "DB_SAVE", "Persistiendo resultado en PostgreSQL (Merge JSONB)"):
            with db_engine.begin() as conn:
                # Si existe un parent_task_id, traer sus datos y hacer pre-merge
                parent_row = conn.execute(
                    text("SELECT parent_task_id FROM idp_smart.document_extractions WHERE task_id = :tid"),
                    {"tid": uuid.UUID(str(task_id))}
                ).fetchone()
                
                if parent_row and parent_row[0]:
                    parent_tid = parent_row[0]
                    parent_data_row = conn.execute(
                        text("SELECT extracted_data FROM idp_smart.document_extractions WHERE task_id = :tid"),
                        {"tid": uuid.UUID(str(parent_tid))}
                    ).fetchone()
                    if parent_data_row and parent_data_row[0]:
                        parent_data = parent_data_row[0]
                        if isinstance(parent_data, str):
                            parent_data = json.loads(parent_data)
                        
                        # Merge semántico a nivel Python de nuevo con lo viejo
                        merged_data = parent_data.copy()
                        for k, v in final_json.items():
                            if v:  # Solo sobrescribir si el nuevo valor no es nulo/vacío
                                merged_data[k] = v
                        final_json = merged_data
                        
                        # Simlify nuevo json
                        simplified = build_simplified_json(final_json)

                # Upsert Semántico en BD usando concatenación JSONB para no perder adendas procesadas por otras vías
                conn.execute(
                    text("""
                        UPDATE idp_smart.document_extractions
                        SET
                            status              = 'COMPLETED',
                            stage_current       = 'COMPLETADO',
                            extracted_data      = COALESCE(extracted_data, '{}'::jsonb) || CAST(:full_json AS jsonb),
                            simplified_json     = :simplified_json,
                            total_duration_s    = EXTRACT(EPOCH FROM (NOW() - started_at)),
                            docling_duration_s  = :docling_t,
                            ai_duration_s       = :ai_t,
                            updated_at          = NOW()
                        WHERE task_id = :task_id
                    """),
                    {
                        "full_json":       json.dumps(final_json),
                        "simplified_json": json.dumps(simplified),
                        "task_id":         uuid.UUID(str(task_id)),
                        "docling_t":       round(docling_duration, 2),
                        "ai_t":            round(ai_duration, 2),
                    },
                )

        log_event(db_engine, task_id, "COMPLETADO",
                  "Tarea finalizada exitosamente.",
                  detail={"campos_llenados": len([v for v in simplified.values() if v])})

        return {"task_id": task_id, "status": "COMPLETED", "fields_filled": len(simplified)}

    except Exception as e:
        stage_at_error = "UNKNOWN"
        try:
            # Intentar obtener el stage actual de la BD si es posible
            with db_engine.connect() as conn:
                row = conn.execute(text("SELECT stage_current FROM idp_smart.document_extractions WHERE task_id = :tid"), {"tid": uuid.UUID(str(task_id))}).fetchone()
                if row:
                    stage_at_error = row[0]
        except:
            pass

        error_msg_raw = str(e)
        error_msg = f"Error en etapa {stage_at_error}: {error_msg_raw}"
        
        # Detector de OOM para VRAM tuning (32GB)
        is_oom = "OutOfMemory" in error_msg_raw or "CUDA out of memory" in error_msg_raw or "OOM" in error_msg_raw.upper()
        if is_oom:
            final_error_status = "ERROR_OOM"
            level = "CRITICAL"
            gpu_desc = gpu_model if "gpu_model" in locals() else "UNKNOWN"
            log_benchmark(
                task_id, pdf_minio_object, f"{gpu_desc}_OOM",
                0.0, 0.0, 0.0, cost_usd=0.0, oom_detected=True
            )
            logger_msg = f"DETECCION OOM: Caida de memoria en GPU. Ajustar gpu_memory_utilization. {error_msg}"
        else:
            final_error_status = f"ERROR_{stage_at_error}"
            level = "ERROR"
            logger_msg = error_msg
            
        print(logger_msg)
        traceback.print_exc()
        log_event(db_engine, task_id, "ERROR", logger_msg, level=level, detail={"traceback": traceback.format_exc(), "stage": stage_at_error, "is_oom": is_oom})
        
        _set_stage(task_id, stage_at_error, status=final_error_status)
        
        try:
            with db_engine.begin() as conn:
                conn.execute(
                    text("UPDATE idp_smart.document_extractions SET error_message = :err WHERE task_id = :tid"),
                    {"err": error_msg[:500], "tid": uuid.UUID(str(task_id))}
                )
        except: pass
        
        return {"task_id": task_id, "status": final_error_status, "error": error_msg, "stage": stage_at_error}


# ── RECOVERY TASK: Re-encola tareas huérfanas ────────────────────────────────
@celery_app.task(name="recover_orphaned_tasks")
def recover_orphaned_tasks():
    """
    Tarea periódica (Celery Beat) que detecta tareas bloqueadas en PENDING_CELERY
    o INICIO sin haber iniciado procesamiento (started_at IS NULL) durante más de
    ORPHAN_RECOVERY_MINUTES minutos, y las re-encola automáticamente.

    Causa raíz típica: fallo silencioso del webhook reactivo de MinIO.
    """
    print(f"🔍 [RECOVERY] Escaneando tareas huérfanas (umbral: {ORPHAN_RECOVERY_MINUTES} min)...")
    recovered = 0
    try:
        with db_engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT task_id, json_minio_path, pdf_minio_path
                    FROM idp_smart.document_extractions
                    WHERE status IN ('PENDING_CELERY', 'INICIO')
                      AND started_at IS NULL
                      AND created_at < NOW() - INTERVAL ':minutes minutes'
                """.replace(':minutes minutes', f'{ORPHAN_RECOVERY_MINUTES} minutes'))
            ).fetchall()

        if not rows:
            print("✅ [RECOVERY] Sin tareas huérfanas.")
            return {"recovered": 0}

        print(f"⚠️  [RECOVERY] {len(rows)} tarea(s) huérfana(s) encontrada(s). Re-encolando...")

        bucket_prefix = "idp-documents/"
        for row in rows:
            tid, json_path, pdf_path = row
            tid_str = str(tid)

            # Limpiar prefijo del bucket para el worker
            clean_pdf  = pdf_path.replace(bucket_prefix, "")  if pdf_path  else ""
            clean_json = json_path.replace(bucket_prefix, "") if json_path else ""

            # Marcar como PENDING_CELERY antes de encolar (reset limpio)
            with db_engine.begin() as conn:
                conn.execute(
                    text("""
                        UPDATE idp_smart.document_extractions
                        SET status = 'PENDING_CELERY', updated_at = NOW()
                        WHERE task_id = :tid
                    """),
                    {"tid": uuid.UUID(tid_str)}
                )

            celery_app.send_task(
                "process_doc",
                args=[tid_str, clean_json, clean_pdf, False],
                task_id=tid_str
            )

            print(f"   🚀 [RECOVERY] Re-encolada: {tid_str} | PDF: {clean_pdf}")
            recovered += 1

    except Exception as e:
        print(f"❌ [RECOVERY] Error durante recuperación: {e}")
        import traceback as _tb
        _tb.print_exc()

    print(f"✅ [RECOVERY] Recuperación completada: {recovered} tarea(s) re-encolada(s).")
    return {"recovered": recovered}
