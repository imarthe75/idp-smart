import uuid
import time
import json
import logging
from io import BytesIO
from minio import Minio
from sqlalchemy import create_engine, text
from core.config import settings
from worker.celery_app import process_doc

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark_runner")

# Archivos de prueba
FILES_SCHEMA = [
    {"pdf": "samples/58beae75-2812-4336-9424-9a0a83628d44.pdf", "code": "BI20"},
    {"pdf": "samples/CCLXXX-01-01-01_01-0380.pdf",              "code": "BI3"},
    {"pdf": "samples/550003_26-11-2021_12-04-25.tif",          "code": "BI1"},
]

# Modelos a probar
MODELS = [
    {"provider": "google",    "model": "gemini-3.1-flash-lite-preview"},
    {"provider": "anthropic", "model": "claude-sonnet-4-6"},
]

sync_url = (
    f"postgresql://{settings.db_user}:{settings.db_password}"
    f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
)
db_engine = create_engine(sync_url)

storage_client = Minio(
    settings.storage_endpoint,
    access_key=settings.storage_access_key,
    secret_key=settings.storage_secret_key,
    secure=settings.storage_secure,
)

def _get_schema(dsactocorta: str):
    with db_engine.connect() as conn:
        result = conn.execute(
            text("SELECT jsconfforma, form_code FROM idp_smart.act_forms_catalog WHERE dsactocorta = :code"),
            {"code": dsactocorta}
        ).fetchone()
        return result

def _upload_to_minio(task_id: str, pdf_path: str, schema_json: dict) -> tuple[str, str]:
    with open(pdf_path, "rb") as f:
        pdf_data = f.read()
    pdf_name = f"{task_id}/{pdf_path.split('/')[-1]}"
    storage_client.put_object(settings.storage_bucket, pdf_name, BytesIO(pdf_data), len(pdf_data))
    
    json_data = json.dumps(schema_json).encode("utf-8")
    json_name = f"{task_id}/form.json"
    storage_client.put_object(settings.storage_bucket, json_name, BytesIO(json_data), len(json_data))
    
    return f"idp-documents/{pdf_name}", f"idp-documents/{json_name}"

def _register_in_db(task_id, pdf_path, json_path, dsactocorta, form_code, provider, model):
    with db_engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO idp_smart.document_extractions
                (task_id, pdf_storage_path, json_storage_path, act_type, form_code, dsactocorta, 
                 llm_provider, llm_model, status, stage_current, created_at)
                VALUES
                (:tid, :pdf, :json, :act, :fcode, :ds, :prov, :mod, 'PENDING_CELERY', 'INICIO', NOW())
            """),
            {"tid": uuid.UUID(task_id), "pdf": pdf_path, "json": json_path, "act": dsactocorta, 
             "fcode": str(form_code), "ds": dsactocorta, "prov": provider, "mod": model}
        )

def run_benchmark(poll_results=False, poll_timeout_s=300, pause_between_s=1.0, acts_filter=None):
    dispatched = []
    for item in FILES_SCHEMA:
        if acts_filter and item["code"] not in acts_filter: continue
        
        row = _get_schema(item["code"])
        if not row: continue
        schema_json, form_code = row
        
        for m in MODELS:
            task_id = str(uuid.uuid4())
            p_obj, j_obj = _upload_to_minio(task_id, item["pdf"], schema_json)
            _register_in_db(task_id, p_obj, j_obj, item["code"], form_code, m["provider"], m["model"])
            
            process_doc.delay(
                task_id=task_id, json_storage_object=j_obj, pdf_storage_path=p_obj,
                skip_vision=False, llm_provider=m["provider"], llm_model=m["model"]
            )
            dispatched.append({"tid": task_id, "act": item["code"], "prov": m["provider"], "mod": m["model"]})
            logger.info(f"🚀 Despachado: {item['code']} | {m['provider']}/{m['model']}")
            time.sleep(pause_between_s)

    if poll_results and dispatched:
        _poll(dispatched, poll_timeout_s)

def _poll(dispatched, timeout):
    logger.info(f"⏳ Polling {len(dispatched)} tareas...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        with db_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT task_id, status FROM idp_smart.document_extractions WHERE task_id = ANY(:ids)"),
                {"ids": [uuid.UUID(d["tid"]) for d in dispatched]}
            ).fetchall()
        
        completed = [r for r in rows if r[1] in ("COMPLETED", "COMPLETADO", "FAILED") or (r[1] or "").startswith("ERROR")]
        if len(completed) >= len(dispatched): break
        time.sleep(10)
    
    print("\n" + "="*50 + "\n RESULTADOS FINALES BENCHMARK \n" + "="*50)
    with db_engine.connect() as conn:
        final_rows = conn.execute(text("SELECT task_id, status, llm_provider, llm_model, dsactocorta FROM idp_smart.document_extractions WHERE task_id = ANY(:ids)"), {"ids": [uuid.UUID(d["tid"]) for d in dispatched]}).fetchall()
    
    for r in final_rows:
        print(f" [{r[1]:12}] {r[4]:5} | {r[2]:10}/{r[3]:30}")

if __name__ == "__main__":
    run_benchmark(poll_results=True, poll_timeout_s=600)
