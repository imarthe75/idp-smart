import uuid
import time
import json
import logging
from io import BytesIO
from minio import Minio
from core.config import settings
from sqlalchemy import create_engine, text
from worker.celery_app import process_doc

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark_runner")

# ── Archivos de prueba (ruta relativa al WORKDIR del contenedor: /app) ────────
FILES_SCHEMA = [
    {"pdf": "samples/58beae75-2812-4336-9424-9a0a83628d44.pdf", "code": "BI20"},
    {"pdf": "samples/CCLXXX-01-01-01_01-0380.pdf",              "code": "BI3"},
]

# ── Modelos a probar ──────────────────────────────────────────────────────────
# NOTA: form_code en la tabla act_forms_catalog es numérico (ej. '24'),
#       pero la búsqueda se hace por dsactocorta (ej. 'BI20') — correcto.
MODELS = [
    {"provider": "google",   "model": "gemini-1.5-flash"},
    {"provider": "google",   "model": "gemini-2.0-flash"},
    {"provider": "groq",     "model": "llama-3.3-70b-versatile"},
    {"provider": "openai",   "model": "qwen/qwen-2.5-72b-instruct"},
    {"provider": "alibaba",  "model": "qwen-plus"},
    {"provider": "openai",   "model": "anthropic/claude-3-5-sonnet"},
]

# ── Clientes de infraestructura ───────────────────────────────────────────────
sync_url = (
    f"postgresql://{settings.db_user}:{settings.db_password}"
    f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
)
db_engine = create_engine(sync_url)
minio_client = Minio(
    settings.minio_endpoint,
    access_key=settings.minio_access_key,
    secret_key=settings.minio_secret_key,
    secure=settings.minio_secure,
)


def _get_schema(dsactocorta: str):
    """
    Obtiene el JSON de schema y el form_code numérico para un acto dado.
    Busca por dsactocorta (ej. 'BI20'), que es la clave usada en el catálogo.
    """
    with db_engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT jsconfforma, form_code, dsactocorta
                FROM idp_smart.act_forms_catalog
                WHERE dsactocorta = :code
            """),
            {"code": dsactocorta},
        ).fetchone()
    return row  # (jsconfforma, form_code_numerico, dsactocorta)


def _upload_to_minio(task_id: str, pdf_path: str, schema_json: dict) -> tuple[str, str]:
    """
    Sube el PDF y el form.json al bucket de MinIO.
    Devuelve (pdf_object_name, json_object_name) — rutas relativas al bucket.
    """
    pdf_name        = pdf_path.split("/")[-1]
    pdf_object_name = f"{task_id}/{pdf_name}"
    json_object_name = f"{task_id}/form.json"

    # Subir PDF
    minio_client.fput_object(settings.minio_bucket, pdf_object_name, pdf_path)

    # Subir Schema JSON
    schema_bytes = json.dumps(schema_json).encode("utf-8")
    minio_client.put_object(
        settings.minio_bucket,
        json_object_name,
        BytesIO(schema_bytes),
        len(schema_bytes),
        content_type="application/json",
    )
    return pdf_object_name, json_object_name


def _register_in_db(
    task_id: str,
    pdf_object_name: str,
    json_object_name: str,
    dsactocorta: str,
    form_code_numerico: str,
):
    """
    Registra la tarea en document_extractions con todos los campos necesarios:
    - act_type      → código corto legible ('BI20')
    - form_code     → código numérico del catálogo ('24')
    - dsactocorta   → mismo que act_type, para compatibilidad con la query del worker
    - json_minio_path → ruta del form.json en MinIO (necesaria para que el AGENT cargue el schema)
    """
    with db_engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO idp_smart.document_extractions
                    (task_id, pdf_minio_path, json_minio_path,
                     act_type, form_code, dsactocorta,
                     status, stage_current, created_at)
                VALUES
                    (:tid, :pdf, :json_path,
                     :act_type, :form_code, :dsactocorta,
                     'PENDING_CELERY', 'INICIANDO BENCHMARK', NOW())
            """),
            {
                "tid":          uuid.UUID(task_id),
                "pdf":          pdf_object_name,
                "json_path":    json_object_name,
                "act_type":     dsactocorta,
                "form_code":    form_code_numerico,
                "dsactocorta":  dsactocorta,
            },
        )


def run_benchmark(
    poll_results: bool = False,
    poll_timeout_s: int = 300,
    pause_between_s: float = 1.0,
):
    """
    Lanza una tarea Celery por cada combinación (archivo × modelo).

    Args:
        poll_results:     Si True, espera a que todas las tareas terminen e imprime un resumen.
        poll_timeout_s:   Tiempo máximo de espera en segundos cuando poll_results=True.
        pause_between_s:  Pausa entre despachos consecutivos para no saturar la cola.
    """
    dispatched: list[dict] = []  # {task_id, dsactocorta, provider, model}

    for item in FILES_SCHEMA:
        pdf_path    = item["pdf"]
        dsactocorta = item["code"]

        # 1. Obtener schema del catálogo
        schema_row = _get_schema(dsactocorta)
        if not schema_row:
            logger.warning(f"Schema '{dsactocorta}' no encontrado en catálogo. Saltando.")
            continue
        schema_json, form_code_numerico, _ = schema_row

        for m in MODELS:
            task_id  = str(uuid.uuid4())
            provider = m["provider"]
            model    = m["model"]

            logger.info(
                f"Despachando → task={task_id[:8]}… | doc={pdf_path.split('/')[-1]}"
                f" | {provider}/{model}"
            )

            try:
                # 2. Subir archivos a MinIO
                pdf_obj, json_obj = _upload_to_minio(task_id, pdf_path, schema_json)

                # 3. Registrar en DB (con TODOS los campos necesarios)
                _register_in_db(
                    task_id,
                    pdf_obj,
                    json_obj,
                    dsactocorta,
                    str(form_code_numerico),
                )

                # 4. Despachar a Celery
                #    Args posicionales: task_id, json_minio_object, pdf_minio_path,
                #                       skip_vision, llm_provider, llm_model
                process_doc.delay(
                    task_id,
                    json_obj,        # ruta relativa al bucket (sin prefijo idp-documents/)
                    pdf_obj,         # ídem
                    False,           # skip_vision=False → OCR completo en primera ejecución
                    provider,
                    model,
                )

                dispatched.append({
                    "task_id":      task_id,
                    "dsactocorta":  dsactocorta,
                    "provider":     provider,
                    "model":        model,
                })

            except Exception as e:
                logger.error(f"Error despachando tarea {task_id[:8]}: {e}")

            time.sleep(pause_between_s)

    logger.info(f"\n✅ Tareas despachadas: {len(dispatched)}")

    # Opcional: polling de resultados
    if poll_results and dispatched:
        _poll_results(dispatched, timeout_s=poll_timeout_s)


def _poll_results(dispatched: list[dict], timeout_s: int = 300):
    """Espera a que todas las tareas terminen e imprime un resumen tabulado."""
    pending_ids = {d["task_id"] for d in dispatched}
    results: dict[str, dict] = {}
    deadline = time.time() + timeout_s

    logger.info(f"⏳ Esperando resultados (timeout={timeout_s}s)…")

    while pending_ids and time.time() < deadline:
        time.sleep(5)
        with db_engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT task_id, status, simplified_json, error_message,
                           docling_duration_s, ai_duration_s, llm_model
                    FROM idp_smart.document_extractions
                    WHERE task_id = ANY(:ids)
                """),
                {"ids": [uuid.UUID(t) for t in list(pending_ids)]},
            ).fetchall()

        for row in rows:
            tid, status, sj, err, d_dur, ai_dur, model = row
            tid_str = str(tid)
            if status in ("COMPLETED", "COMPLETADO", "FAILED") or (status or "").startswith("ERROR"):
                results[tid_str] = {
                    "status": status,
                    "simplified_json": sj,
                    "error": err,
                    "docling_s": d_dur,
                    "ai_s": ai_dur,
                    "model": model,
                }
                pending_ids.discard(tid_str)

    # Imprimir resumen
    print("\n" + "=" * 80)
    print(" RESULTADOS BENCHMARK ")
    print("=" * 80)
    for d in dispatched:
        tid = d["task_id"]
        res = results.get(tid, {})
        status = res.get("status", "TIMEOUT")
        sj = res.get("simplified_json") or {}
        n_campos = len([v for v in sj.values() if v is not None]) if sj else 0
        err = res.get("error", "")
        print(
            f"  [{status:12}] {d['dsactocorta']:5} | {d['provider']:10}/{d['model']:40}"
            f" | campos={n_campos:3} | err={'SI' if err else 'NO'}"
        )
    print("=" * 80)

    if pending_ids:
        logger.warning(f"Tareas sin completar (timeout): {pending_ids}")


if __name__ == "__main__":
    run_benchmark(poll_results=True, poll_timeout_s=600)
