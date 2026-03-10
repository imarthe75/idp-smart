from celery import Celery
from core.config import settings
from sqlalchemy import create_engine, text
import json

# Import Engine Components
from engine.vision import extract_markdown_from_minio
from engine.agent import extract_form_data
from engine.mapper import get_json_schema, map_results_to_json

# Create celery application
celery_app = Celery(
    "idp_worker",
    broker=settings.valkey_url,
    backend=settings.valkey_url
)

# Optional configuration
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'], 
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    worker_concurrency=4 # Depending on the 16-core machine mentioned in requirements
)

# We use sync engine for Celery as it runs synchronously
sync_database_url = settings.database_url.replace("postgresql+asyncpg", "postgresql")
db_engine = create_engine(sync_database_url)

@celery_app.task(name="process_doc")
def process_doc(task_id: str, json_minio_object: str, pdf_minio_object: str):
    """
    Simulated task for processing documents using Granite-Docling VLM and LangChain Agents.
    Fetches from MinIO, processes, and updates the PostgreSQL Database.
    """
    print(f"Task {task_id}: Started processing document {pdf_minio_object} against form {json_minio_object}...")
    
    # 1. Vision: Convert PDF to Markdown Hierarchical format via Docling
    print(f"[{task_id}] Running Granite-Docling Extract...")
    doc_markdown = extract_markdown_from_minio("idp-documents", pdf_minio_object)
    
    if not doc_markdown:
        # Fallback if docling fails or is not installed properly
        doc_markdown = "# Documento Fallback\nNo se pudo extraer contenido jerárquico."
    
    # 2. Context Merger: Load the JSON form to know what fields to extract
    print(f"[{task_id}] Loading base JSON form schema for dynamic mapping...")
    schema = get_json_schema("idp-documents", json_minio_object)
    
    # 3. Agent: Ask LangChain to find the values
    print(f"[{task_id}] Prompting LangChain Agent for Semantic Extraction...")
    extracted_key_val = extract_form_data(doc_markdown, schema)
    
    # 4. Mapper: Inject extracted data into `value` of JSON
    print(f"[{task_id}] Mapping dynamic UUIDs onto target JSON array...")
    final_json = map_results_to_json(schema, extracted_key_val)
    
    # Save the extracted data to database using SQLAlchemy Sync Engine
    print(f"[{task_id}] Updating Result in Database...")
    with db_engine.begin() as conn:
        conn.execute(
            text("UPDATE idp_smart.document_extractions SET status = 'COMPLETED', extracted_data = :data WHERE task_id = :task_id"),
            {"data": json.dumps(final_json), "task_id": task_id}
        )
    
    return {
        "task_id": task_id,
        "status": "COMPLETED"
    }
