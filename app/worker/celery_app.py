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
import hashlib
import redis
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
    def wrapper(*args, **kwargs):
        import uuid
        
        # 1. Extraer task_id (puede estar en args[0], args[1] si es bound, o en kwargs)
        task_id = kwargs.get("task_id")
        if not task_id:
            # Si es bound, args[0] es self, y args[1] es task_id
            if hasattr(args[0], 'request'):
                task_id = args[1] if len(args) > 1 else None
            else:
                task_id = args[0] if len(args) > 0 else None
        
        if not task_id:
             # Si no hay task_id, no podemos monitorear, pero no rompemos la ejecución
             return func(*args, **kwargs)

        # 2. Extraer provider/model para registro inicial (opcional)
        provider = kwargs.get("llm_provider", settings.llm_provider)
        model = kwargs.get("llm_model", settings.current_llm_model)
        
        start_t = time.time()
        try:
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
                        "p": provider, 
                        "m": model,
                        "tid": uuid.UUID(str(task_id))
                    }
                )
        except Exception as e:
            print(f"Error registrando inicio de performance: {e}")

        try:
            return func(*args, **kwargs)
        except Exception as e:
            # Registrar error si la tarea falla
            with db_engine.begin() as conn:
                conn.execute(
                    text("UPDATE idp_smart.document_extractions SET status = 'FAILED', error_message = :err WHERE task_id = :tid"),
                    {"err": str(e), "tid": uuid.UUID(str(task_id))}
                )
            raise e
        finally:
            elapsed = time.time() - start_t
            try:
                with db_engine.begin() as conn:
                    conn.execute(
                        text("UPDATE idp_smart.document_extractions SET total_duration_s = :e WHERE task_id = :tid"),
                        {"e": round(elapsed, 2), "tid": uuid.UUID(str(task_id))}
                    )
            except: pass
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


def _set_stage(task_id: str, stage: str, status: str = None, provider: str = None, model: str = None):
    """Actualiza stage_current y status en la BD."""
    try:
        with db_engine.begin() as conn:
            # Si no se especifica status, forzamos PROCESSING si el stage avanza
            final_status = status or "PROCESSING"
            final_provider = provider or settings.llm_provider
            final_model = model or settings.current_llm_model
            
            # Buscamos si ya tiene started_at, sino lo ponemos ahora
            conn.execute(
                text("""
                    UPDATE idp_smart.document_extractions
                    SET stage_current = :stage, 
                        status = :status, 
                        updated_at = NOW(),
                        started_at = COALESCE(started_at, NOW()),
                        llm_provider = :llm_provider, 
                        llm_model = :llm_model
                    WHERE task_id = :task_id
                """),
                {
                    "stage": stage, 
                    "status": final_status,
                    "task_id": uuid.UUID(str(task_id)),
                    "llm_provider": final_provider,
                    "llm_model": final_model
                },
            )
    except Exception as exc:
        print(f"No se pudo actualizar stage_current a {stage}: {exc}")


@celery_app.task(name="process_doc", bind=True, max_retries=10)
@monitor_performance
def process_doc(self, task_id: str, json_minio_object: str, pdf_minio_path: str, skip_vision: bool = False, llm_provider: str = None, llm_model: str = None, form_code: str = None):
    """
    Pipeline principal coordinado con caché de OCR y soporte mutimodal.
    """
    pdf_minio_object = pdf_minio_path  # Inicialización inmediata
    
    if not pdf_minio_object:
        log_event(db_engine, task_id, "ERROR", "pdf_minio_path es None o vacío. Abortando.")
        _set_stage(task_id, "ERROR", status="FAILED")
        return {"status": "FAILED", "error": "pdf_minio_path requerido"}
    if not json_minio_object:
        log_event(db_engine, task_id, "ERROR", "json_minio_object es None o vacío. Abortando.")
        _set_stage(task_id, "ERROR", status="FAILED")
        return {"status": "FAILED", "error": "json_minio_object requerido"}

    final_provider = llm_provider or settings.llm_provider
    final_model = llm_model or settings.current_llm_model

    bucket_prefix = f"{settings.minio_bucket}/"
    if json_minio_object.startswith(bucket_prefix):
        json_minio_object = json_minio_object[len(bucket_prefix):]
    if pdf_minio_object.startswith(bucket_prefix):
        pdf_minio_object = pdf_minio_object[len(bucket_prefix):]

    minio_client = get_minio_client()
    doc_markdown = None
    docling_duration = 0.0
    ai_duration = 0.0
    p_count = 0
    gpu_model = "CPU"
    image_paths = []

    act_short = None
    try:
        log_event(db_engine, task_id, "INICIO", f"Extrayendo: {pdf_minio_object} con {final_provider}/{final_model}")
        
        # Obtener dsactocorta para contexto legal
        with db_engine.connect() as conn:
            res_meta = conn.execute(text("SELECT dsactocorta FROM idp_smart.document_extractions WHERE task_id = :tid"), {"tid": uuid.UUID(str(task_id))}).fetchone()
            if res_meta: act_short = res_meta[0]

        # --- [OPTIMIZACIÓN: CACHÉ SEMÁNTICA POR HASH (SHA-256)] ---
        pdf_hash = None
        try:
            # Descargar PDF a temporal para calcular Hash
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False) as tmp_pdf:
                minio_client.fget_object(settings.minio_bucket, pdf_minio_object, tmp_pdf.name)
                # Calcular Hash
                sha256_hash = hashlib.sha256()
                with open(tmp_pdf.name, "rb") as f:
                    for byte_block in iter(lambda: f.read(4096), b""):
                        sha256_hash.update(byte_block)
                pdf_hash = sha256_hash.hexdigest()
                os.remove(tmp_pdf.name)

            # 1. Comprobar en MinIO si ya existe el Markdown para este HASH
            md_cache_path = f"ocr_cache/{pdf_hash}.md"
            r = redis.from_url(settings.valkey_url)
            
            # --- [MEJORA: CIERRE DISTRIBUIDO (LOCK) PARA EVITAR OCR SIMULTÁNEO] ---
            lock_key = f"ocr_lock:{pdf_hash}"
            with r.lock(lock_key, timeout=1800):  # 30 minutos de lock
                try:
                    # Volver a comprobar existencia DENTRO del lock
                    minio_client.stat_object(settings.minio_bucket, md_cache_path)
                    resp_md = minio_client.get_object(settings.minio_bucket, md_cache_path)
                    doc_markdown = resp_md.read().decode("utf-8")
                    p_count = doc_markdown.count("\f") + 1
                    skip_vision = True
                    log_event(db_engine, task_id, "VISION", f"🚀 CACHE HIT (Lock Protected): Hash [{pdf_hash[:10]}]")
                except:
                    # Si no existe, procedemos a la extracción pero marcamos que este hilo tiene el lock
                    logger.info(f"🔒 [OCR LOCK] No hay caché para {pdf_hash[:10]}. Iniciando OCR...")
                    skip_vision = False
        except Exception as e:
            logger.warning(f"Error en búsqueda de caché/lock: {e}")

        # --- ETAPA 1: VISION / OCR ---
        if not skip_vision:
            _set_stage(task_id, "VISION", provider=final_provider, model=final_model)
            docling_start = time.time()
            with timed_stage(db_engine, task_id, "VISION", "Extracción Docling"):
                log_event(db_engine, task_id, "VISION", f"Docling sobre {pdf_minio_object}")
                doc_markdown, p_count, gpu_model = extract_markdown_from_minio(pdf_minio_object)
            docling_duration = time.time() - docling_start
            
            if doc_markdown:
                try:
                    md_bytes = doc_markdown.encode("utf-8")
                    from io import BytesIO
                    # Guardar en carpeta de tarea
                    minio_client.put_object(settings.minio_bucket, f"{task_id}/extracted.md", BytesIO(md_bytes), len(md_bytes))
                    # Guardar en CACHE GLOBAL por HASH
                    if pdf_hash:
                         minio_client.put_object(settings.minio_bucket, f"ocr_cache/{pdf_hash}.md", BytesIO(md_bytes), len(md_bytes))
                         logger.info(f"💾 [OCR CACHE] Markdown persistido para Hash: {pdf_hash}")
                    
                    with db_engine.begin() as conn:
                        conn.execute(
                            text("UPDATE idp_smart.document_extractions SET markdown_minio_path = :p, page_count = :pc WHERE task_id = :tid"),
                            {"p": f"{task_id}/extracted.md", "pc": p_count, "tid": uuid.UUID(str(task_id))}
                        )
                except Exception as e:
                    logger.error(f"Fallo al guardar markdown: {e}")
        else:
            _set_stage(task_id, "VISION", status="COMPLETED", provider=final_provider, model=final_model)
            if doc_markdown: p_count = doc_markdown.count("\f") + 1

        # --- ETAPA 2: AGENT (Razonamiento) ---
        _set_stage(task_id, "AGENT", provider=final_provider, model=final_model)
        
        try:
            resp_s = minio_client.get_object(settings.minio_bucket, json_minio_object)
            schema = json.loads(resp_s.read().decode("utf-8"))
        except Exception as e:
            log_event(db_engine, task_id, "ERROR", f"No se pudo cargar el esquema: {e}")
            raise e

        # Sampling multimodal
        if final_provider in ["google", "openai"] and pdf_minio_object.lower().endswith((".pdf", ".tif")):
            try:
                import fitz
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                    minio_client.fget_object(settings.minio_bucket, pdf_minio_object, tmp_pdf.name)
                    doc = fitz.open(tmp_pdf.name)
                    indices = [0, 1, len(doc)//2, len(doc)-1] if len(doc) > 4 else range(len(doc))
                    for idx in indices:
                        if idx < len(doc):
                            page = doc.load_page(idx)
                            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                            img_p = f"/tmp/{task_id}_p{idx}.png"
                            pix.save(img_p)
                            image_paths.append(img_p)
                    doc.close()
                    os.remove(tmp_pdf.name)
            except Exception as e:
                print(f"Error en sampling visual: {e}")

        ai_start = time.time()
        with timed_stage(db_engine, task_id, "AGENT", f"Reasoning {final_model}"):
            res = extract_form_data(markdown_content=doc_markdown, json_schema=schema, image_paths=image_paths, llm_provider=final_provider, llm_model=final_model, act_id=act_short)
            extracted_data = res.get("fields", {}) if isinstance(res, dict) else (res or {})
        ai_duration = time.time() - ai_start
        
        for p in image_paths:
            if os.path.exists(p): os.remove(p)

        # --- ETAPA 3: MAPEO Y CIERRE ---
        _set_stage(task_id, "MAPPER", provider=final_provider, model=final_model)
        from engine.agent import create_simplified_json
        simplified = create_simplified_json(extracted_data, schema)
        with db_engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE idp_smart.document_extractions SET 
                    status='COMPLETED', stage_current='COMPLETADO',
                    extracted_data=CAST(:fj AS jsonb),
                    simplified_json=CAST(:sj AS jsonb),
                    docling_duration_s=:dt, ai_duration_s=:at,
                    updated_at=NOW()
                    WHERE task_id=:tid
                """),
                {
                    "fj": json.dumps(extracted_data), 
                    "sj": json.dumps(simplified), 
                    "dt": round(docling_duration, 2), 
                    "at": round(ai_duration, 2), 
                    "tid": uuid.UUID(str(task_id))
                }
            )
        
        # --- [PERSISTENCIA EN MINIO] ---
        try:
            temp_sj = f"/tmp/{task_id}_sj.json"
            temp_rj = f"/tmp/{task_id}_rj.json"
            with open(temp_sj, "w") as f: json.dump(simplified, f)
            with open(temp_rj, "w") as f: json.dump(extracted_data, f)
            upload_file_to_minio(temp_sj, f"{task_id}/simplified.json")
            upload_file_to_minio(temp_rj, f"{task_id}/result.json")
            if os.path.exists(temp_sj): os.remove(temp_sj)
            if os.path.exists(temp_rj): os.remove(temp_rj)
        except Exception as e:
            print(f"Error subiendo JSONs a MinIO: {e}")
        
        # --- [GUARDAR EN CACHÉ AL TERMINAR] ---
        if pdf_hash:
            try:
                r = redis.from_url(settings.valkey_url)
                cache_payload = {
                    "markdown": doc_markdown,
                    "extracted_data": extracted_data,
                    "pages": p_count,
                    "model": final_model
                }
                # Cachear por 7 días
                r.setex(f"idp_cache:{pdf_hash}", 3600 * 24 * 7, json.dumps(cache_payload))
            except Exception as e:
                print(f"Error al guardar cache: {e}")

        log_event(db_engine, task_id, "COMPLETADO", f"Finalizado en {round(docling_duration + ai_duration, 1)}s")
        return {"status": "SUCCESS"}

    except Exception as e:
        import traceback
        log_event(db_engine, task_id, "ERROR", f"Pipeline falló: {str(e)}", detail=traceback.format_exc())
        _set_stage(task_id, "ERROR", status="FAILED")
        return {"status": "FAILED", "error": str(e)}
    finally:
        import gc
        gc.collect()

@celery_app.task(name="recover_orphaned_tasks")
def recover_orphaned_tasks():
    # Simplificado para salud del sistema
    return {"status": "OK"}
