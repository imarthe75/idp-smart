from celery import Celery
from core.config import settings
from core.idp_logger import log_event, timed_stage, build_simplified_json
from core.minio_client import get_minio_client, upload_file_to_minio
from sqlalchemy import create_engine, text
import json
import os
import traceback
import uuid
import logging
import gc
import time
from functools import wraps

# Logging
logger = logging.getLogger("idp-smart")

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
    worker_concurrency=settings.worker_concurrency,
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

def monitor_performance(func):
    """Decorador para registrar métricas de rendimiento en la BD."""
    @wraps(func)
    def wrapper(task_id, *args, **kwargs):
        start_t = time.time()
        with db_engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE idp_smart.document_extractions 
                    SET started_at = NOW(), 
                        llm_provider = :p,
                        llm_model = :m 
                    WHERE task_id = :tid
                """),
                {
                    "p": settings.llm_provider, 
                    "m": settings.current_llm_model,
                    "tid": uuid.UUID(str(task_id))
                }
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
    except Exception as e:
        print(f"Error guardando benchmark: {e}")


def _set_stage(task_id: str, stage: str, status: str = None):
    """Actualiza stage_current y status en la BD."""
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
                        SET stage_current = :stage, updated_at = NOW(), 
                            llm_provider = :llm_provider, llm_model = :llm_model
                        WHERE task_id = :task_id
                    """),
                    {
                        "stage": stage, 
                        "task_id": uuid.UUID(str(task_id)), 
                        "llm_provider": settings.llm_provider,
                        "llm_model": settings.current_llm_model
                    },
                )
    except Exception as exc:
        print(f"No se pudo actualizar stage_current a {stage}: {exc}")


@celery_app.task(name="process_doc")
@monitor_performance
def process_doc(task_id: str, json_minio_object: str, pdf_minio_object: str, skip_vision: bool = False):
    """
    Pipeline principal coordinado.
    """
    bucket_prefix = f"{settings.minio_bucket}/"
    if json_minio_object.startswith(bucket_prefix):
        json_minio_object = json_minio_object[len(bucket_prefix):]
    if pdf_minio_object.startswith(bucket_prefix):
        pdf_minio_object = pdf_minio_object[len(bucket_prefix):]

    minio_client = get_minio_client()
    doc_markdown = None
    docling_duration = 0.0
    vision_duration = 0.0
    ai_duration = 0.0
    p_count = 0
    gpu_model = "CPU"
    visual_analysis = ""
    inference_cost_usd = 0.0
    image_paths = []
    cached_vision = None

    try:
        # ── INICIO ──
        log_event(db_engine, task_id, "INICIO", f"Iniciando: {pdf_minio_object}")

        # Reutilización de Markdown
        with db_engine.connect() as conn:
            result = conn.execute(
                text("SELECT markdown_minio_path, parent_task_id FROM idp_smart.document_extractions WHERE task_id = :tid"),
                {"tid": uuid.UUID(str(task_id))}
            ).fetchone()
            
            target_md_path = None
            if result:
                target_md_path = result[0]
                parent_tid = result[1]
                if not target_md_path and parent_tid:
                    res_parent = conn.execute(
                        text("SELECT markdown_minio_path FROM idp_smart.document_extractions WHERE task_id = :tid"),
                        {"tid": uuid.UUID(str(parent_tid))}
                    ).fetchone()
                    if res_parent:
                        target_md_path = res_parent[0]

            if target_md_path and skip_vision:
                try:
                    obj_path = target_md_path.split("idp-documents/")[-1]
                    res_md = minio_client.get_object("idp-documents", obj_path)
                    doc_markdown = res_md.read().decode("utf-8")
                    log_event(db_engine, task_id, "INICIO", "Markdown reutilizado con éxito.")
                except:
                    skip_vision = False
            
            # --- CACHE DE VISIÓN (Búsqueda Proactiva) ---
            if parent_tid and not skip_vision:
                try:
                    v_cache_path = f"{parent_tid}/vision_analysis.txt"
                    res_v = minio_client.get_object(settings.minio_bucket, v_cache_path)
                    cached_vision = res_v.read().decode("utf-8")
                    log_event(db_engine, task_id, "INICIO", "Análisis visual recuperado del cache del padre.")
                except: pass

        # ── PASO 1: VISION (Docling) ──
        force_multimodal = settings.vision_skip_local_ocr and not skip_vision
        
        if not skip_vision and not force_multimodal:
            docling_start = time.time()
            _set_stage(task_id, "VISION")
            with timed_stage(db_engine, task_id, "VISION", "Extracción Docling"):
                log_event(db_engine, task_id, "VISION", f"Ejecutando Docling sobre {pdf_minio_object}")
                doc_markdown, p_count, gpu_model = extract_markdown_from_minio(pdf_minio_object)
                docling_duration = time.time() - docling_start
                
                with db_engine.begin() as conn:
                    conn.execute(
                        text("UPDATE idp_smart.document_extractions SET page_count = :pc, gpu_model = :gpu WHERE task_id = :tid"),
                        {"pc": p_count, "gpu": gpu_model, "tid": uuid.UUID(str(task_id))},
                    )

                if not doc_markdown:
                    doc_markdown = "# Documento\nContenido vacío."
                log_event(db_engine, task_id, "VISION", f"Docling finalizado ({p_count} pág).")

        elif force_multimodal:
            _set_stage(task_id, "VISION")
            log_event(db_engine, task_id, "VISION", "Modo Multimodal DIRECTO (Skip Docling)")
            try:
                import fitz
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_p:
                    minio_client.fget_object(settings.minio_bucket, pdf_minio_object, tmp_p.name)
                    doc_p = fitz.open(tmp_p.name)
                    p_count = len(doc_p)
                    doc_p.close()
                    os.remove(tmp_p.name)
            except: p_count = 1
            
            gpu_model = "CLOUD-MULTIMODAL"
            doc_markdown = "# VISION SKIP"
            with db_engine.begin() as conn:
                conn.execute(
                    text("UPDATE idp_smart.document_extractions SET page_count = :pc, gpu_model = :gpu WHERE task_id = :tid"),
                    {"pc": p_count, "gpu": gpu_model, "tid": uuid.UUID(str(task_id))},
                )

        # ── PASO 2: QWEN VISION (RunPod) ──
        if settings.llm_provider == "runpod" and not skip_vision:
            _set_stage(task_id, "QWEN_VISION")
            with timed_stage(db_engine, task_id, "QWEN_VISION", "Qwen2-VL Analysis"):
                vision_start = time.time()
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    minio_client.fget_object(settings.minio_bucket, pdf_minio_object, tmp.name)
                    visual_analysis = extract_visual_analysis_sync(tmp.name)
                    os.remove(tmp.name)
                vision_duration = time.time() - vision_start
                if visual_analysis:
                    doc_markdown = (doc_markdown or "") + "\n" + visual_analysis
                    # Persistir en Cache para reuso
                    try:
                        v_bytes = visual_analysis.encode("utf-8")
                        upload_file_to_minio(minio_client, f"{task_id}/vision_analysis.txt", v_bytes, "text/plain")
                    except: pass
        
        elif cached_vision:
            visual_analysis = cached_vision
            if visual_analysis:
                doc_markdown = (doc_markdown or "") + "\n" + visual_analysis
                log_event(db_engine, task_id, "QWEN_VISION", "Usando evidencia visual del cache.")

        # ── PERSISTENCIA MARKDOWN ──
        if doc_markdown and not skip_vision:
            try:
                md_bytes = doc_markdown.encode("utf-8")
                md_path = upload_file_to_minio(minio_client, f"{task_id}/extracted.md", md_bytes, "text/markdown")
                with db_engine.begin() as conn:
                    conn.execute(
                        text("UPDATE idp_smart.document_extractions SET markdown_minio_path = :path WHERE task_id = :tid"),
                        {"path": md_path, "tid": task_id}
                    )
            except: pass

        # ── PASO 3: AGENT (Reasoning) ──
        _set_stage(task_id, "AGENT")
        schema = get_json_schema(settings.minio_bucket, json_minio_object)
        
        # Muestreo multimodal si aplica
        force_images = settings.vision_skip_local_ocr or not doc_markdown or len(doc_markdown) < 100
        if settings.llm_provider in ("google", "openai") and (not skip_vision or force_images):
            try:
                import fitz
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                    minio_client.fget_object(settings.minio_bucket, pdf_minio_object, tmp_pdf.name)
                    doc = fitz.open(tmp_pdf.name)
                    indices = [0, 1, len(doc)//2, len(doc)-1] if len(doc) > 4 else range(len(doc))
                    for idx in indices:
                        if idx < len(doc):
                            page = doc.load_page(idx)
                            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                            img_path = f"/tmp/{task_id}_p{idx}.png"
                            pix.save(img_path)
                            image_paths.append(img_path)
                    doc.close()
                    os.remove(tmp_pdf.name)
            except: pass

        ai_start = time.time()
        with timed_stage(db_engine, task_id, "AGENT", f"Reasoning {settings.llm_provider}"):
            res = extract_form_data(doc_markdown, schema, visual_analysis=visual_analysis, image_paths=image_paths)
            if isinstance(res, dict) and "fields" in res:
                extracted_data = res["fields"]
            else:
                extracted_data = res
        ai_duration = time.time() - ai_start
        
        for p in image_paths:
            if os.path.exists(p): os.remove(p)

        # ── PASO 4: MAPPER & SAVE ──
        _set_stage(task_id, "MAPPER")
        final_json = map_results_to_json(schema, extracted_data)
        simplified = build_simplified_json(final_json)

        _set_stage(task_id, "DB_SAVE")
        with db_engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE idp_smart.document_extractions SET 
                    status='COMPLETED', stage_current='COMPLETADO',
                    extracted_data=COALESCE(extracted_data, '{}'::jsonb) || CAST(:fj AS jsonb),
                    simplified_json=:sj, updated_at=NOW(),
                    docling_duration_s=:dt, ai_duration_s=:at
                    WHERE task_id=:tid
                """),
                {"fj": json.dumps(final_json), "sj": json.dumps(simplified), "dt": round(docling_duration, 2), "at": round(ai_duration, 2), "tid": uuid.UUID(str(task_id))}
            )

        log_event(db_engine, task_id, "COMPLETADO", "Proceso finalizado.")
        log_benchmark(task_id, pdf_minio_object, gpu_model, docling_duration, vision_duration, ai_duration)
        
        return {"status": "SUCCESS", "task_id": task_id}

    except Exception as e:
        error_msg = f"Error: {str(e)}"
        log_event(db_engine, task_id, "ERROR", error_msg, level="ERROR", detail=traceback.format_exc())
        _set_stage(task_id, "ERROR", status="FAILED")
        return {"status": "FAILED", "error": error_msg}
    finally:
        gc.collect()

@celery_app.task(name="recover_orphaned_tasks")
def recover_orphaned_tasks():
    # Simplificado para salud del sistema
    return {"status": "OK"}
