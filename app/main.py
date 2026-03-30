from fastapi import FastAPI, Depends, File, UploadFile, BackgroundTasks, Form, Request
import logging
import json
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from db.database import get_db, engine
from db.models import Base, DocumentExtraction
from worker.celery_app import celery_app
from celery.result import AsyncResult
from core.minio_client import get_minio_client, upload_file_to_minio
from core.utils import generate_uuidv7
import uuid

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="idp-smart API",
    description="Intelligent Document Processing - Extracción semántica y llenado automatizado de formas registrales y notariales.",
    version="1.0.0"
)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("idp-smart")

# CORS: acepta cualquier origen en redes privadas RFC-1918 y localhost.
# Funciona sin importar la IP del servidor de desarrollo o producción.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    
    Hace un JOIN entre `cfdeffrmpre` e `ctactos` para retornar:
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
            jsconfforma,
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

@app.get("/api/v1/benchmarks", tags=["Administración"])
async def get_hardware_benchmarks(db: AsyncSession = Depends(get_db)):
    """
    Retorna métricas de rendimiento por GPU para análisis de infraestructura.
    """
    query = text("""
        SELECT 
            b.*,
            e.act_type,
            e.page_count
        FROM idp_smart.hardware_benchmarks b
        LEFT JOIN idp_smart.document_extractions e ON b.task_id = e.task_id
        ORDER BY b.created_at DESC
        LIMIT 100
    """)
    result = await db.execute(query)
    benchmarks = [dict(row._mapping) for row in result.fetchall()]
    return {"total": len(benchmarks), "benchmarks": benchmarks}

@app.post("/api/v1/process", tags=["Procesamiento"])
async def process_document(
    act_type: str = Form(...),
    form_code: str = Form(...),
    json_form: UploadFile = File(...),
    document: UploadFile | None = File(None),
    additional_documents: list[UploadFile] = File(None),
    reuse_task_id: str | None = Form(None),
    expediente_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Endpoint para procesar uno o varios documentos.
    """
    # LIMPIEZA PREVENTIVA: Asegurar que no hay objetos "viejos" en la sesión
    await db.rollback()
    db.expunge_all()
    
    task_id = generate_uuidv7()
    print(f"DEBUG: Generated task_id={task_id} type={type(task_id)}")
    minio_client = get_minio_client()
    
    # 1. Guardar esquema JSON (VALIDACIÓN PREVENTIVA)
    try:
        json_content = await json_form.read()
        json.loads(json_content) # Validar que sea JSON válido
    except Exception as e_json:
        return {"error": f"El archivo 'json_form' no contiene un JSON válido: {str(e_json)}", "code": 400}
        
    json_object_name = f"{task_id}/form.json"
    upload_file_to_minio(minio_client, json_object_name, json_content, "application/json")
    json_minio_path = f"idp-documents/{json_object_name}"

    pdf_minio_path = None
    pdf_object_name = None
    additional_paths = []
    parent_task_id = None
    skip_vision = False

    # 2. Manejo de Documento Principal / Reúso
    if reuse_task_id:
        query = text("SELECT pdf_minio_path, markdown_minio_path, page_count FROM idp_smart.document_extractions WHERE task_id = :tid")
        result = await db.execute(query, {"tid": uuid.UUID(reuse_task_id)})
        original = result.fetchone()
        if original:
            pdf_minio_path = original[0]
            pdf_object_name = pdf_minio_path.split("idp-documents/")[-1]
            parent_task_id = uuid.UUID(reuse_task_id)
            skip_vision = True if original[1] else False
            p_count = original[2] or 0
    
    if not pdf_minio_path and document and document.filename:
        doc_content = await document.read()
        
        # --- Cálculo inmediato de páginas ---
        try:
            import io
            from pypdf import PdfReader
            pdf_buf = io.BytesIO(doc_content)
            reader = PdfReader(pdf_buf)
            p_count = len(reader.pages)
        except: p_count = 0

        pdf_object_name = f"{task_id}/{document.filename}"
        pdf_minio_path = upload_file_to_minio(minio_client, pdf_object_name, doc_content, document.content_type)
        
        # Si el usuario no dio expediente, usamos el nombre del archivo
        if not expediente_id:
            expediente_id = document.filename

    # 3. Manejo de Documentos Adicionales
    if additional_documents:
        for doc in additional_documents:
            if doc and doc.filename:
                content = await doc.read()
                obj_name = f"{task_id}/additional/{doc.filename}"
                path = upload_file_to_minio(minio_client, obj_name, content, doc.content_type)
                additional_paths.append(path)

    if not pdf_minio_path:
        return {"error": "Debes proporcionar un 'document' o un 'reuse_task_id' válido.", "code": 400}

    # 4. Registro en DB (USO DE SQL PURO CON CASTING PARA EVITAR ERRORES DE TIPO EN PG18)
    import json
    # IMPORTANTE: Limpiar cualquier objeto previo en la sesión para evitar INSERTs automáticos fallidos
    db.expunge_all()
    
    insert_query = text("""
        INSERT INTO idp_smart.document_extractions 
            (task_id, expediente_id, act_type, form_code, pdf_minio_path, json_minio_path, 
             additional_docs, parent_task_id, status, stage_current, page_count)
        VALUES 
            (CAST(:task_id AS UUID), :expediente, :act_type, :form_code, :pdf_path, :json_path, 
             CAST(:add_docs AS JSONB), CAST(:parent_tid AS UUID), :status, :stage, :p_count)
    """)
    
    print(f"DEBUG DB: Inserting task_id={task_id} expediente={expediente_id}")
    
    await db.execute(insert_query, {
        "task_id":     str(task_id),
        "expediente":  expediente_id,
        "act_type":    act_type,
        "form_code":   form_code,
        "pdf_path":    pdf_minio_path,
        "json_path":   json_minio_path,
        "add_docs":    json.dumps(additional_paths),
        "parent_tid":  str(parent_task_id) if parent_task_id else None,
        "status":      "PENDING_CELERY",
        "stage":       "INICIO",
        "p_count":     p_count
    })
    await db.commit()
    
    # 5. Enviar a Celery (DESACTIVADO: Ahora lo hace MinIO reactivamente vía Webhook)
    # A menos que sea un reúso (que no dispara evento de upload en MinIO)
    if reuse_task_id:
        celery_app.send_task(
            "process_doc", 
            args=[str(task_id), json_object_name, pdf_object_name, skip_vision], 
            task_id=str(task_id)
        )
        logger.info(f"Manual dispatch for reuse: {task_id}")
    else:
        logger.info(f"Waiting for MinIO reactive trigger for task: {task_id}")
    
    return {
        "status": "Accepted",
        "task_id": task_id,
        "message": "Encolado para procesamiento",
        "reusing_from": parent_task_id,
        "additional_docs_count": len(additional_paths)
    }

@app.post("/api/v1/reprocess/{task_id}", tags=["Procesamiento"])
async def reprocess_document(
    task_id: str,
    skip_vision: bool = False,
    additional_documents: list[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Reinicia un proceso de extracción para un TaskID existente.
    Si skip_vision=True, intentará usar el Markdown ya existente en MinIO.
    Se pueden subir adendas para integrarlas al reprocesamiento.
    """
    query = text("SELECT * FROM idp_smart.document_extractions WHERE task_id = :task_id")
    result = await db.execute(query, {"task_id": task_id})
    row = result.fetchone()

    if not row:
        return {"error": "Tarea no encontrada."}

    row_dict = dict(row._mapping)
    additional_paths = row_dict.get("additional_docs") or []
    minio_client = get_minio_client()

    # Manejar documentos adicionales nuevos
    if additional_documents:
        for doc in additional_documents:
            if doc and doc.filename:
                content = await doc.read()
                obj_name = f"{task_id}/additional/{doc.filename}"
                path = upload_file_to_minio(minio_client, obj_name, content, doc.content_type)
                if path not in additional_paths:
                    additional_paths.append(path)
                skip_vision = False  # Forzar vision si hay nuevos documentos

    import json
    new_additional_docs_json = json.dumps(additional_paths)

    # Reset status a PENDING_CELERY para que el worker lo tome.
    update_query = text("""
        UPDATE idp_smart.document_extractions 
        SET status = 'PENDING_CELERY', 
            stage_current = 'INICIO', 
            additional_docs = :add_docs,
            created_at = NOW(),
            updated_at = NOW() 
        WHERE task_id = :task_id
    """)
    await db.execute(update_query, {"task_id": task_id, "add_docs": new_additional_docs_json})
    await db.commit()

    # Re-enviar a Celery
    pdf_obj = row_dict["pdf_minio_path"].split("idp-documents/")[-1]
    json_obj = row_dict["json_minio_path"].split("idp-documents/")[-1] if row_dict.get("json_minio_path") else f"{task_id}/form.json"

    celery_app.send_task(
        "process_doc", 
        args=[task_id, json_obj, pdf_obj, skip_vision], 
        task_id=task_id
    )

    return {
        "status": "Accepted",
        "task_id": task_id,
        "message": f"Task re-queued (skip_vision={skip_vision})"
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
        "error_message":        row_dict.get("error_message"),
        "act_type":             row_dict["act_type"],
        "form_code":            row_dict["form_code"],
        "pdf_path":             row_dict["pdf_minio_path"],
        "markdown_minio_path":  row_dict.get("markdown_minio_path"),
        "created_at":           str(row_dict.get("created_at", "")),
        "updated_at":           str(row_dict.get("updated_at", "")),
        "extracted_data":       row_dict["extracted_data"],
        "simplified_json":      row_dict["simplified_json"],
    }


_STAGE_ORDER = {
    "PENDING_CELERY": 0, "INICIO": 5,
    "VISION": 15, "SCHEMA_LOAD": 30,
    "AGENT": 65, "MAPPER": 80,
    "SIMPLIFY": 90, "DB_SAVE": 95,
    "COMPLETADO": 100, "COMPLETED": 100, "ERROR": 100,
}

# Mapeo de errores específicos a sus etiquetas
_ERROR_LABELS = {
    "ERROR_VISION": "Error en extracción de contenido (VISION).",
    "ERROR_SCHEMA_LOAD": "Error cargando esquema de la forma.",
    "ERROR_AGENT": "Error en extracción semántica con IA.",
    "ERROR_MAPPER": "Error mapeando datos extraídos.",
    "ERROR_DB_SAVE": "Error guardando resultados en BD.",
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


@app.get("/api/v1/extractions", tags=["Procesamiento"])
async def list_extractions(limit: int = 100, db: AsyncSession = Depends(get_db)):
    """
    Lista las extracciones procesadas recientemente.
    """
    query = text("""
        SELECT task_id, expediente_id, status, stage_current, act_type, form_code, pdf_minio_path, 
               markdown_minio_path, created_at, updated_at, error_message,
               docling_duration_s, ai_duration_s, total_duration_s, llm_provider, page_count
        FROM idp_smart.document_extractions
        ORDER BY created_at DESC
        LIMIT :limit
    """)
    result = await db.execute(query, {"limit": limit})
    rows = result.fetchall()
    
    extractions = [dict(row._mapping) for row in rows]
    # Convert datetimes to string for JSON serialization
    for ext in extractions:
        ext["created_at"] = str(ext["created_at"])
        ext["updated_at"] = str(ext["updated_at"])
        
    return {"total": len(extractions), "extractions": extractions}


@app.delete("/api/v1/extractions/{task_id}", tags=["Procesamiento"])
async def delete_extraction(task_id: str, db: AsyncSession = Depends(get_db)):
    """
    Elimina el registro de una extracción, sus logs y sus archivos en MinIO.
    También cancela la tarea en Celery si está pendiente o en ejecución.
    """
    # 0. Cancelar tarea en Celery
    try:
        from worker.celery_app import celery_app as celery
        celery.control.revoke(task_id, terminate=True, signal='SIGKILL')
        print(f"🛑 [Celery] Tarea {task_id} revocada/cancelada.")
    except Exception as e_celery:
        print(f"⚠️ Error al revocar tarea Celery {task_id}: {e_celery}")

    # 1. Eliminar archivos en MinIO
    try:
        minio_client = get_minio_client()
        objects_to_delete = minio_client.list_objects(
            "idp-documents", prefix=f"{task_id}/", recursive=True
        )
        for obj in objects_to_delete:
            minio_client.remove_object("idp-documents", obj.object_name)
    except Exception as e:
        print(f"Error limpiando MinIO para {task_id}: {e}")

    # 2. Eliminar métricas y logs relacionados
    await db.execute(
        text("DELETE FROM idp_smart.hardware_benchmarks WHERE task_id = :task_id"),
        {"task_id": task_id}
    )
    await db.execute(
        text("DELETE FROM idp_smart.process_logs WHERE task_id = :task_id"),
        {"task_id": task_id}
    )

    # 3. Eliminar registro principal
    query = text("DELETE FROM idp_smart.document_extractions WHERE task_id = :task_id")
    result = await db.execute(query, {"task_id": task_id})
    await db.commit()
    
    if result.rowcount == 0:
        return {"error": "Tarea no encontrada."}
        
    return {"status": "Deleted", "task_id": task_id, "cleanup": "MinIO, Logs and Celery Revoked"}


@app.get("/api/v1/progress/{task_id}", tags=["Monitoreo"])
async def get_progress(task_id: str, db: AsyncSession = Depends(get_db)):
    """
    Endpoint ligero para polling del frontend.
    Devuelve el porcentaje de avance, la etapa actual, tiempo transcurrido
    y si el sistema puede recibir nuevas tareas en paralelo.

    **Diseñado para llamarse cada 3-5 segundos desde el frontend.**
    """
    query = text("""
        SELECT task_id, expediente_id, status, stage_current, act_type, form_code,
               created_at, updated_at, started_at, total_duration_s, llm_provider, 
               page_count, pdf_minio_path
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
    started_at = row_dict.get("started_at")
    total_duration = row_dict.get("total_duration_s")

    # Calcular porcentaje basado en la etapa actual
    pct = _STAGE_ORDER.get(stage, _STAGE_ORDER.get(status, 0))
    if status.startswith("ERROR"):
        pct = 100

    # Determinar etiqueta de etapa
    stage_label = _STAGE_LABELS.get(stage, stage)
    if status.startswith("ERROR"):
        stage_label = _ERROR_LABELS.get(status, _STAGE_LABELS.get("ERROR"))

    finished = status in ("COMPLETED", "COMPLETADO", "ERROR") or status.startswith("ERROR")

    # Calcular tiempo transcurrido (SOLO si ya inició en el worker)
    from datetime import datetime, timezone
    elapsed_s = 0
    if started_at:
        if total_duration and finished:
            elapsed_s = int(total_duration)
        else:
            now = datetime.now(timezone.utc)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            elapsed_s = int((now - started_at).total_seconds())

    # Estimación de tiempo restante basada en % y tiempo transcurrido desde el inicio real
    estimated_remaining_s = None
    if 0 < pct < 100 and elapsed_s > 0 and not finished:
        estimated_remaining_s = int((elapsed_s / pct) * (100 - pct))

    # Extraer nombre de archivo de la ruta de MinIO
    file_name = row_dict.get("pdf_minio_path", "").split('/')[-1] if row_dict.get("pdf_minio_path") else None

    return {
        "task_id":               task_id,
        "status":                status,
        "stage_current":         stage,
        "stage_label":           stage_label,
        "progress_pct":          pct,
        "elapsed_seconds":       elapsed_s,
        "estimated_remaining_s": estimated_remaining_s,
        "total_duration_s":      total_duration,
        "is_waiting":            started_at is None and not finished,
        "finished":              finished,
        "can_submit_more":       True,
        "act_type":              row_dict.get("act_type"),
        "form_code":             row_dict.get("form_code"),
        "llm_provider":          row_dict.get("llm_provider"),
        "page_count":            row_dict.get("page_count"),
        "file_name":             row_dict.get("expediente_id") or file_name,
        "expediente":            row_dict.get("expediente_id")
    }




@app.get("/api/v1/full/{task_id}", tags=["Procesamiento"])
async def get_full_json(task_id: str, db: AsyncSession = Depends(get_db)):
    """
    Retorna el JSON completo (esquema Java poblado) de una tarea.
    """
    query = text("SELECT extracted_data FROM idp_smart.document_extractions WHERE task_id = :task_id")
    result = await db.execute(query, {"task_id": task_id})
    row = result.fetchone()
    if not row or not row[0]:
        return {"error": "JSON completo no encontrado para esta tarea."}
    return row[0]


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
        SELECT task_id, status, act_type, form_code, simplified_json, error_message, stage_current
        FROM idp_smart.document_extractions
        WHERE task_id = :task_id
    """)
    result = await db.execute(query, {"task_id": task_id})
    row = result.fetchone()

    if not row:
        return {"error": "Tarea no encontrada."}

    row_dict = dict(row._mapping)
    
    # Caso: La tarea falló oficialmente
    if row_dict["status"].startswith("ERROR_"):
        return {
            "task_id": row_dict["task_id"],
            "status": row_dict["status"],
            "message": row_dict.get("error_message") or f"Error en etapa {row_dict.get('stage_current')}",
            "simplified_json": None
        }

    # Caso: Aún no termina (no hay json ni error)
    if row_dict.get("simplified_json") is None:
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

@app.get("/api/v1/document/pdf/{task_id}", tags=["Visualización"])
async def view_pdf(task_id: str, db: AsyncSession = Depends(get_db)):
    """Busca el PDF original en MinIO y lo sirve al navegador."""
    query = text("SELECT pdf_minio_path FROM idp_smart.document_extractions WHERE task_id = :tid")
    result = await db.execute(query, {"tid": uuid.UUID(task_id)})
    row = result.fetchone()
    if not row or not row[0]:
        return {"error": "Documento no encontrado", "code": 404}
    
    minio_client = get_minio_client()
    obj_name = row[0].split("idp-documents/")[-1]
    
    try:
        response = minio_client.get_object("idp-documents", obj_name)
        return StreamingResponse(response, media_type="application/pdf")
    except Exception as e:
        return {"error": str(e), "code": 500}

@app.get("/api/v1/document/markdown/{task_id}", tags=["Visualización"])
async def view_markdown(task_id: str, db: AsyncSession = Depends(get_db)):
    """Busca el Markdown extraído en MinIO y lo sirve como texto."""
    query = text("SELECT markdown_minio_path FROM idp_smart.document_extractions WHERE task_id = :tid")
    result = await db.execute(query, {"tid": uuid.UUID(task_id)})
    row = result.fetchone()
    if not row or not row[0]:
        return {"error": "Markdown no encontrado", "code": 404}
    
    minio_client = get_minio_client()
    obj_name = row[0].split("idp-documents/")[-1]
    
    try:
        response = minio_client.get_object("idp-documents", obj_name)
        return StreamingResponse(response, media_type="text/markdown; charset=utf-8")
    except Exception as e:
        return {"error": str(e), "code": 500}

@app.post("/api/v1/internal/minio-event", tags=["Internal"])
async def minio_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Recibe notificaciones de MinIO cuando un archivo se sube al bucket.
    Solo reacciona a PDFs (ignora form.json, markdown, etc.) para evitar
    disparos duplicados. Incluye retry para manejar el race condition entre
    el commit del INSERT y la llegada del evento.
    """
    import asyncio
    from urllib.parse import unquote

    try:
        data = await request.json()
        records = data.get("Records", [])
        for record in records:
            s3 = record.get("s3")
            if not s3:
                continue

            bucket = s3["bucket"]["name"]
            key = s3["object"]["key"]
            clean_key = unquote(key)

            logger.info(f"🔔 MinIO Event: {record.get('eventName')} en {bucket}/{clean_key}")

            # ── Solo procesar PDFs — ignorar form.json, markdown, imágenes ──────
            # Esto elimina disparos duplicados y el race condition del form.json
            if not clean_key.lower().endswith(".pdf"):
                logger.info(f"⏭️ Ignorando archivo no-PDF: {clean_key}")
                continue

            # ── Retry: esperar hasta 3s a que el INSERT de la API sea visible ───
            # Race condition: MinIO puede notificar ANTES de que la API haga commit.
            row = None
            for attempt in range(1, 7):  # 6 intentos × 0.5s = hasta 3s de espera
                await db.rollback()  # limpiar caché de la sesión para ver datos nuevos
                query = text("""
                    SELECT task_id, act_type, form_code, json_minio_path, status
                    FROM idp_smart.document_extractions
                    WHERE pdf_minio_path LIKE :path
                """)
                result = await db.execute(query, {"path": f"%{clean_key}"})
                row = result.fetchone()
                if row:
                    break
                logger.info(f"⏳ Intento {attempt}/6: tarea aún no en DB, esperando 0.5s... ({clean_key})")
                await asyncio.sleep(0.5)

            if not row:
                logger.warning(f"⚠️ PDF {clean_key} subido pero no se encontró tarea en DB tras 3s. El Beat la recuperará.")
                continue

            task_id, act_type, form_code, json_minio_path, status = row

            # Solo disparamos si está en estado inicial (evitar bucles)
            if status in ("INICIO", "PENDING_CELERY"):
                logger.info(f"🚀 Disparando procesamiento REACTIVO para tarea {task_id}")
                # Marcar como PENDING_CELERY y encolar
                await db.execute(
                    text("UPDATE idp_smart.document_extractions SET status = 'PENDING_CELERY', updated_at = NOW() WHERE task_id = :tid"),
                    {"tid": task_id}
                )
                await db.commit()

                celery_app.send_task(
                    "process_doc",
                    args=[str(task_id), json_minio_path, clean_key, False],
                    task_id=str(task_id)
                )
            else:
                logger.info(f"⏭️ Tarea {task_id} ya tiene estado {status}, saltando trigger.")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"❌ Error en MinIO Webhook: {str(e)}")
        return {"status": "error", "detail": str(e)}
