from fastapi import FastAPI, Depends, File, UploadFile, BackgroundTasks, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from db.database import get_db, engine
from db.models import Base, DocumentExtraction
from worker.celery_app import celery_app
from celery.result import AsyncResult
from core.minio_client import get_minio_client, upload_file_to_minio
import uuid

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="idp-smart API",
    description="Intelligent Document Processing - Extracción semántica y llenado automatizado de formas registrales y notariales.",
    version="1.0.0"
)

# CORS: acepta cualquier origen en redes privadas RFC-1918 y localhost.
# Funciona sin importar la IP del servidor de desarrollo o producción.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"http://(localhost|127\.0\.0\.1"          # loopback
        r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"         # Red clase A privada (10.x.x.x)
        r"|192\.168\.\d{1,3}\.\d{1,3}"             # Red clase C privada (192.168.x.x)
        r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"  # Red clase B privada (172.16-31.x.x)
        r")(:\d+)?"                                 # Cualquier puerto opcional
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

@app.get("/api/v1/forms", tags=["Catálogos"])
async def get_pre_coded_forms(db: AsyncSession = Depends(get_db)):
    """
    Obtiene la lista de tipos de acto disponibles para procesar.
    
    Hace un JOIN entre `cfdeffrmpre` y `ctactos` para retornar:
    - `form_code`: Código corto del acto (ej. `BI34`).
    - `dsactocorta`: Nombre corto del tipo de acto (ej. `BI34`).
    - `dsacto`: Descripción completa del tipo de acto (ej. `Primera Inscripción`).
    - `display_label`: Etiqueta lista para mostrar en el dropdown (ej. `BI34 - Primera Inscripción`).
    """
    query = text("""
        SELECT 
            form_code,
            lldeffrmpre,
            llacto,
            dsactocorta,
            dsacto,
            CONCAT(dsactocorta, ' - ', dsacto) AS display_label
        FROM idp_smart.act_forms_catalog
        ORDER BY dsactocorta ASC
    """)
    result = await db.execute(query)

    acts = []
    for row in result.fetchall():
        row_dict = dict(row._mapping)
        acts.append(row_dict)

    return {"total": len(acts), "acts": acts}

@app.post("/api/v1/process", tags=["Procesamiento"])
async def process_document(
    act_type: str = Form(..., description="Código corto del tipo de acto (dsactocorta), ej: BI34"),
    form_code: str = Form(..., description="Código de la forma a llenar (cdforma), ej: BI34"),
    json_form: UploadFile = File(..., description="JSON precodificado vacío de la forma (jsconfforma)"),
    document: UploadFile = File(..., description="Documento a procesar: PDF, imagen (PNG/JPG) o archivo Office"),
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

@app.get("/api/v1/status/{task_id}", tags=["Procesamiento"])
async def get_status(task_id: str, db: AsyncSession = Depends(get_db)):
    """
    Consulta el estado de una tarea de extracción.
    Retorna el JSON completo (extracted_data), el JSON simplificado (simplified_json)
    y la ruta al markdown generado por Docling (markdown_minio_path).
    """
    query = text("SELECT * FROM idp_smart.document_extractions WHERE task_id = :task_id")
    result = await db.execute(query, {"task_id": task_id})
    row = result.fetchone()

    if not row:
        return {"error": "Tarea no encontrada. Verifica el task_id."}

    row_dict = dict(row._mapping)
    return {
        "task_id":              row_dict["task_id"],
        "status":               row_dict["status"],
        "stage_current":        row_dict.get("stage_current"),
        "act_type":             row_dict["act_type"],
        "form_code":            row_dict["form_code"],
        "pdf_path":             row_dict["pdf_minio_path"],
        "markdown_minio_path":  row_dict.get("markdown_minio_path"),
        "created_at":           str(row_dict.get("created_at", "")),
        "updated_at":           str(row_dict.get("updated_at", "")),
        "extracted_data":       row_dict["extracted_data"],
        "simplified_json":      row_dict["simplified_json"],
    }


# Orden y peso de cada etapa para cálculo de progreso
_STAGE_ORDER = {
    "PENDING_CELERY": 0, "INICIO": 5,
    "VISION": 15, "SCHEMA_LOAD": 30,
    "AGENT": 65, "MAPPER": 80,
    "SIMPLIFY": 90, "DB_SAVE": 95,
    "COMPLETADO": 100, "COMPLETED": 100, "ERROR": 100,
}

_STAGE_LABELS = {
    "PENDING_CELERY": "En cola, esperando worker…",
    "INICIO": "Iniciando proceso…",
    "VISION": "Extrayendo contenido del documento (Docling)…",
    "SCHEMA_LOAD": "Cargando esquema de la forma…",
    "AGENT": "Extracción semántica con IA (etapa más larga)…",
    "MAPPER": "Mapeando datos a los campos UUID…",
    "SIMPLIFY": "Generando resumen de campos extraídos…",
    "DB_SAVE": "Guardando resultado en base de datos…",
    "COMPLETADO": "¡Proceso completado exitosamente!",
    "COMPLETED": "¡Proceso completado exitosamente!",
    "ERROR": "Se produjo un error en el proceso.",
}


@app.get("/api/v1/progress/{task_id}", tags=["Monitoreo"])
async def get_progress(task_id: str, db: AsyncSession = Depends(get_db)):
    """
    Endpoint ligero para polling del frontend.
    Devuelve el porcentaje de avance, la etapa actual, tiempo transcurrido
    y si el sistema puede recibir nuevas tareas en paralelo.

    **Diseñado para llamarse cada 3-5 segundos desde el frontend.**
    """
    query = text("""
        SELECT task_id, status, stage_current, act_type, form_code,
               created_at, updated_at
        FROM idp_smart.document_extractions
        WHERE task_id = :task_id
    """)
    result = await db.execute(query, {"task_id": task_id})
    row = result.fetchone()

    if not row:
        return {"error": "Tarea no encontrada."}

    row_dict = dict(row._mapping)
    status = row_dict["status"]
    stage  = row_dict.get("stage_current") or status

    # Calcular porcentaje basado en la etapa actual
    pct = _STAGE_ORDER.get(stage, _STAGE_ORDER.get(status, 0))

    # Calcular tiempo transcurrido
    from datetime import datetime, timezone
    created = row_dict.get("created_at")
    elapsed_s = 0
    if created:
        now = datetime.now(timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        elapsed_s = int((now - created).total_seconds())

    # Estimación de tiempo restante basada en % y tiempo transcurrido
    estimated_remaining_s = None
    if 0 < pct < 100 and elapsed_s > 0:
        estimated_remaining_s = int((elapsed_s / pct) * (100 - pct))

    finished = status in ("COMPLETED", "COMPLETADO", "ERROR")

    return {
        "task_id":               task_id,
        "status":                status,
        "stage_current":         stage,
        "stage_label":           _STAGE_LABELS.get(stage, stage),
        "progress_pct":          pct,
        "elapsed_seconds":       elapsed_s,
        "estimated_remaining_s": estimated_remaining_s,
        "finished":              finished,
        # El sistema es asíncrono — siempre se pueden enviar más documentos
        "can_submit_more":       True,
        "act_type":              row_dict.get("act_type"),
        "form_code":             row_dict.get("form_code"),
    }




@app.get("/api/v1/simplified/{task_id}", tags=["Procesamiento"])
async def get_simplified_json(task_id: str, db: AsyncSession = Depends(get_db)):
    """
    Retorna únicamente el JSON simplificado `{label: value}` de una tarea completada.
    Útil para revisión rápida de los datos extraídos sin el ruido de UUIDs y metadatos.

    Ejemplo de respuesta:
    ```json
    {
      "Folio real electrónico": "12345678901234567890",
      "No. Notario": "42",
      "Fecha de escritura": "2024-03-15"
    }
    ```
    """
    query = text("""
        SELECT task_id, status, act_type, form_code, simplified_json
        FROM idp_smart.document_extractions
        WHERE task_id = :task_id
    """)
    result = await db.execute(query, {"task_id": task_id})
    row = result.fetchone()

    if not row:
        return {"error": "Tarea no encontrada."}

    row_dict = dict(row._mapping)
    if not row_dict.get("simplified_json"):
        return {
            "task_id": row_dict["task_id"],
            "status": row_dict["status"],
            "message": "El JSON simplificado aún no está disponible. La tarea puede estar en proceso.",
            "simplified_json": None
        }

    return {
        "task_id":        row_dict["task_id"],
        "status":         row_dict["status"],
        "act_type":       row_dict["act_type"],
        "form_code":      row_dict["form_code"],
        "simplified_json": row_dict["simplified_json"],
    }


@app.get("/api/v1/logs/{task_id}", tags=["Monitoreo"])
async def get_execution_logs(task_id: str, db: AsyncSession = Depends(get_db)):
    """
    Retorna el log completo de ejecución de una tarea ordenado cronológicamente.
    Incluye cada etapa del proceso: VISION, SCHEMA_LOAD, AGENT, MAPPER, SIMPLIFY, DB_SAVE.

    Campos por evento:
    - `stage`: Etapa del proceso.
    - `level`: INFO | WARNING | ERROR.
    - `message`: Descripción del evento.
    - `detail`: Métricas adicionales (campos llenados, duración, errores...).
    - `duration_ms`: Tiempo en milisegundos que tomó la etapa.
    - `created_at`: Timestamp del evento.
    """
    logs_query = text("""
        SELECT stage, level, message, detail, duration_ms, created_at
        FROM idp_smart.process_logs
        WHERE task_id = :task_id
        ORDER BY created_at ASC, id ASC
    """)
    logs_result = await db.execute(logs_query, {"task_id": task_id})
    logs = [dict(row._mapping) for row in logs_result.fetchall()]

    if not logs:
        return {"task_id": task_id, "message": "No hay logs para esta tarea.", "logs": []}

    # Calcular duración total del proceso
    total_duration_ms = sum(
        log["duration_ms"] for log in logs if log.get("duration_ms")
    )

    return {
        "task_id":          task_id,
        "total_events":     len(logs),
        "total_duration_ms": round(total_duration_ms, 2),
        "total_duration_s":  round(total_duration_ms / 1000, 2),
        "logs": [
            {
                "stage":       log["stage"],
                "level":       log["level"],
                "message":     log["message"],
                "detail":      log["detail"],
                "duration_ms": log["duration_ms"],
                "created_at":  str(log["created_at"]),
            }
            for log in logs
        ],
    }


@app.get("/api/v1/logs", tags=["Monitoreo"])
async def get_recent_logs(
    limit: int = 50,
    level: str | None = None,
    stage: str | None = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Consulta los logs más recientes de todas las tareas.
    Permite filtrar por `level` (INFO, WARNING, ERROR) y por `stage`.
    """
    filters = "WHERE 1=1"
    params: dict = {"limit": limit}
    if level:
        filters += " AND level = :level"
        params["level"] = level.upper()
    if stage:
        filters += " AND stage = :stage"
        params["stage"] = stage.upper()

    query = text(f"""
        SELECT task_id, stage, level, message, detail, duration_ms, created_at
        FROM idp_smart.process_logs
        {filters}
        ORDER BY created_at DESC
        LIMIT :limit
    """)
    result = await db.execute(query, params)
    logs = [dict(row._mapping) for row in result.fetchall()]

    return {
        "total": len(logs),
        "filters": {"level": level, "stage": stage, "limit": limit},
        "logs": [
            {
                "task_id":     log["task_id"],
                "stage":       log["stage"],
                "level":       log["level"],
                "message":     log["message"],
                "detail":      log["detail"],
                "duration_ms": log["duration_ms"],
                "created_at":  str(log["created_at"]),
            }
            for log in logs
        ],
    }
