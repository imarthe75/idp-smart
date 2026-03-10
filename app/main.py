from fastapi import FastAPI, Depends, File, UploadFile, BackgroundTasks, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from db.database import get_db, engine
from db.models import Base, DocumentExtraction
from worker.celery_app import celery_app
from celery.result import AsyncResult
from core.minio_client import get_minio_client, upload_file_to_minio
import uuid

app = FastAPI(
    title="idp-smart API", 
    description="Intelligent Document Processing - Extraction and automated form filling",
    version="1.0.0"
)

@app.on_event("startup")
async def startup_event():
    # Attempt to create tables if they do not exist
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS idp_smart"))
        await conn.run_sync(Base.metadata.create_all)
        
@app.get("/")
def read_root():
    return {"message": "Welcome to idp-smart API"}

@app.get("/api/v1/forms")
async def get_pre_coded_forms(db: AsyncSession = Depends(get_db)):
    """Fetch the list of forms from the cfdeffrmpre table in rpp_qa"""
    query = text("SELECT * FROM public.cfdeffrmpre")
    result = await db.execute(query)
    
    # We fetch only the keys to return a manageable list
    forms = []
    for row in result.fetchall():
        row_dict = dict(row._mapping)
        forms.append(row_dict)
        
    return {"forms_count": len(forms), "forms_preview": forms[:10]}

@app.post("/api/v1/process")
async def process_document(
    act_type: str = Form(...),
    form_code: str = Form(...),
    json_form: UploadFile = File(...),
    document: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Endpoint for uploading a complex document (PDF/Image) along with the initial JSON form.
    Returns a TaskID for asynchronous polling via Valkey/Celery.
    """
    task_id = str(uuid.uuid4())
    
    # Read files in memory
    doc_content = await document.read()
    json_content = await json_form.read()
    
    # Initialize MinIO client
    minio_client = get_minio_client()
    
    # Upload to MinIO
    pdf_object_name = f"{task_id}/{document.filename}"
    pdf_minio_path = upload_file_to_minio(minio_client, pdf_object_name, doc_content, document.content_type)
    
    # Upload json to MinIO for Celery to fetch
    json_object_name = f"{task_id}/{json_form.filename}"
    upload_file_to_minio(minio_client, json_object_name, json_content, "application/json")
    
    # Create DB entry for tracking the extraction
    new_extraction = DocumentExtraction(
        task_id=task_id,
        act_type=act_type,
        form_code=form_code,
        pdf_minio_path=pdf_minio_path,
        status="PENDING_CELERY"
    )
    db.add(new_extraction)
    await db.commit()
    
    # Send to Celery queue, passing minio references instead of local tmp paths
    celery_app.send_task(
        "process_doc", 
        args=[task_id, json_object_name, pdf_object_name], 
        task_id=task_id
    )
    
    return {
        "status": "Accepted",
        "task_id": task_id,
        "message": "Document uploaded to MinIO and queued for processing",
        "minio_path": pdf_minio_path
    }

@app.get("/api/v1/status/{task_id}")
async def get_status(task_id: str, db: AsyncSession = Depends(get_db)):
    """
    Check the status of an ongoing IDP extraction task directly from the Database.
    """
    query = text("SELECT * FROM idp_smart.document_extractions WHERE task_id = :task_id")
    result = await db.execute(query, {"task_id": task_id})
    row = result.fetchone()
    
    if not row:
        return {"error": "Task not found in the database. It may not exist."}
        
    row_dict = dict(row._mapping)
    return {
        "task_id": row_dict["task_id"],
        "status": row_dict["status"],
        "act_type": row_dict["act_type"],
        "form_code": row_dict["form_code"],
        "pdf_path": row_dict["pdf_minio_path"],
        "result": row_dict["extracted_data"]
    }
