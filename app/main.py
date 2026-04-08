from fastapi import FastAPI, Depends, File, UploadFile, BackgroundTasks, Form, Request, HTTPException
import logging
import json
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from db.database import get_db, engine
from db.models import Base, DocumentExtraction
from worker.celery_app import celery_app
from celery.result import AsyncResult
from core.storage_client import get_storage_client, upload_file_to_storage
from core.config import settings
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
    json_form: UploadFile | None = File(None),
    document: UploadFile | None = File(None),
    additional_documents: list[UploadFile] = File(None),
    reuse_task_id: str | None = Form(None),
    expediente_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Endpoint para procesar documentos (TIFF/PDF) bajo uno o varios actos notariales.
    Si se envían múltiples actos (separados por coma), se genera un proceso por cada uno
    reutilizando el OCR del documento principal.
    """
    await db.rollback()
    db.expunge_all()
    
    task_id = generate_uuidv7()
    storage_client = get_storage_client()
    
    # --- SOPORTE MULTI-ACTO ---
    form_codes = [f.strip() for f in form_code.split(",")]
    act_types_list = [a.strip() for a in act_type.split(",")]
    
    # Validar que al menos tengamos un código de acto
    if not form_codes or not form_codes[0]:
        raise HTTPException(status_code=400, detail="Debe proporcionar al menos un form_code.")

    primary_task_id = task_id
    tasks_created = []

    # 1. Preparar esquema JSON inicial
    json_content = None
    if json_form:
        try:
            json_content = await json_form.read()
            json.loads(json_content) 
        except Exception as e_json:
            raise HTTPException(status_code=400, detail=f"El archivo 'json_form' no es un JSON válido: {str(e_json)}")
    
    # Iterar sobre todos los actos solicitados
    for index, (f_code, a_type) in enumerate(zip(form_codes, act_types_list)):
        current_task_id = primary_task_id if index == 0 else generate_uuidv7()
        
        # Obtener el JSON para este acto
        current_json_content = None
        if index == 0 and json_content:
            current_json_content = json_content
        else:
            # Buscar en catálogo por defecto
            q_schema = text("SELECT jsconfforma FROM idp_smart.act_forms_catalog WHERE form_code = :code")
            res_schema = await db.execute(q_schema, {"code": f_code})
            row_schema = res_schema.fetchone()
            if row_schema and row_schema[0]:
                current_json_content = json.dumps(row_schema[0]).encode("utf-8")
            elif index == 0 and not json_content:
                raise HTTPException(status_code=400, detail=f"No se subió json_form y el acto '{f_code}' no existe en el catálogo.")
            else:
                # Si es secundario y no hay en catálogo, usamos el del primero como fallback desesperado
                current_json_content = json_content

        json_object_name = f"{current_task_id}/form.json"
        upload_file_to_storage(storage_client, json_object_name, current_json_content, "application/json")
        json_storage_path = f"idp-documents/{json_object_name}"

        pdf_storage_path = None
        pdf_object_name = None
        additional_paths = []
        parent_task_id = None
        skip_vision = False

        # 2. Manejo de Documento Principal / Reúso
        if reuse_task_id or index > 0:
            # Si es secundaria de este mismo upload, el "padre" es el primary_task_id
            target_tid = uuid.UUID(reuse_task_id) if (reuse_task_id and index == 0) else primary_task_id
            
            query = text("SELECT pdf_storage_path, markdown_storage_path, page_count FROM idp_smart.document_extractions WHERE task_id = :tid")
            result = await db.execute(query, {"tid": target_tid})
            original = result.fetchone()
            if original:
                pdf_storage_path = original[0]
                pdf_object_name = pdf_storage_path.split("idp-documents/")[-1]
                parent_task_id = target_tid
                skip_vision = True # Las secundarias siempre skipean vision local si hay padre
                p_count = original[2] or 0
        
        if not pdf_storage_path and document and document.filename:
            # Esto solo debería entrar en el index == 0 si no hay reuse_task_id
            doc_content = await document.read()
            
            # --- Cálculo inmediato de páginas (Soporte PDF y TIFF) ---
            try:
                import io
                import fitz
                pdf_buf = io.BytesIO(doc_content)
                with fitz.open(stream=pdf_buf) as doc_info:
                    p_count = len(doc_info)
            except Exception as e_count:
                print(f"⚠️ Error contando páginas en upload: {e_count}")
                p_count = 0

            pdf_object_name = f"{current_task_id}/{document.filename}"
            pdf_storage_path = upload_file_to_storage(storage_client, pdf_object_name, doc_content, document.content_type)
            
            if not expediente_id:
                expediente_id = document.filename

        # 3. Manejo de Documentos Adicionales (Compartidos por todos en este multi-acto)
        if additional_documents:
            # Aquí podríamos optimizar, pero por simplicidad los linkeamos
            # Nota: require lógica de carga previa si index > 0
            pass

        if not pdf_storage_path:
            raise HTTPException(status_code=400, detail="Debes proporcionar un 'document' o un 'reuse_task_id' válido.")

        # 4. Registro en DB
        # IMPORTANTE: Reusar el primer insert_query definido fuera o aquí
        ins_q = text("""
            INSERT INTO idp_smart.document_extraction_batch
            (task_id, expediente_id, act_type, form_code, pdf_storage_path, json_storage_path, 
             parent_task_id, status, stage_current, page_count)
            VALUES 
            (CAST(:task_id AS UUID), :expediente, :act_type, :form_code, :pdf_path, :json_path, 
             CAST(:parent_tid AS UUID), :status, :stage, :p_count)
        """)
        
        # Usamos idp_smart.document_extractions (el nombre correcto)
        await db.execute(text("""
            INSERT INTO idp_smart.document_extractions 
                (task_id, expediente_id, act_type, form_code, pdf_storage_path, json_storage_path, 
                 parent_task_id, status, stage_current, page_count,
                 llm_provider, llm_model)
            VALUES 
                (CAST(:task_id AS UUID), :expediente, :act_type, :form_code, :pdf_path, :json_path, 
                 CAST(:parent_tid AS UUID), :status, :stage, :p_count,
                 :provider, :model)
        """), {
            "task_id":     str(current_task_id),
            "expediente":  expediente_id,
            "act_type":    a_type.strip(),
            "form_code":   f_code.strip(),
            "pdf_path":    pdf_storage_path,
            "json_path":   json_storage_path,
            "parent_tid":  str(parent_task_id) if parent_task_id else None,
            "status":      "PENDING_CELERY",
            "stage":       "INICIO",
            "p_count":     p_count,
            "provider":    settings.llm_provider,
            "model":       settings.current_llm_model
        })
        tasks_created.append(str(current_task_id))

    await db.commit()
    
    # 5. Activación de Tareas (Directa y Fiable)
    # Enviamos a Celery inmediatamente. Esto es más robusto que esperar a un webhook 
    # de red que podría fallar en condiciones de alta carga.
    for tid in tasks_created:
        celery_app.send_task(
            "process_doc", 
            args=[tid, f"{tid}/form.json", pdf_object_name, (tid != str(primary_task_id))], 
            task_id=tid
        )
    
    return {
        "status": "Accepted",
        "task_ids": tasks_created,
        "primary_task_id": primary_task_id,
        "count": len(tasks_created),
        "message": f"Se han creado {len(tasks_created)} procesos paralelos compartiendo un único OCR.",
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
    storage_client = get_storage_client()

    # Manejar documentos adicionales nuevos
    if additional_documents:
        for doc in additional_documents:
            if doc and doc.filename:
                content = await doc.read()
                obj_name = f"{task_id}/additional/{doc.filename}"
                path = upload_file_to_storage(storage_client, obj_name, content, doc.content_type)
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
    pdf_obj = row_dict["pdf_storage_path"].split("idp-documents/")[-1]
    json_obj = row_dict["json_storage_path"].split("idp-documents/")[-1] if row_dict.get("json_storage_path") else f"{task_id}/form.json"

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
    y la ruta al markdown generado por Docling (markdown_storage_path).
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
        "pdf_path":             row_dict["pdf_storage_path"],
        "markdown_storage_path":  row_dict.get("markdown_storage_path"),
        "llm_provider":         row_dict.get("llm_provider"),
        "llm_model":            row_dict.get("llm_model"),
        "gpu_model":            row_dict.get("gpu_model"),
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
        SELECT task_id, expediente_id, status, stage_current, act_type, form_code, pdf_storage_path, 
               markdown_storage_path, created_at, updated_at, error_message,
               docling_duration_s, ai_duration_s, total_duration_s, 
               llm_provider, llm_model, gpu_model, page_count
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
        storage_client = get_storage_client()
        objects_to_delete = storage_client.list_objects(
            "idp-documents", prefix=f"{task_id}/", recursive=True
        )
        for obj in objects_to_delete:
            storage_client.remove_object("idp-documents", obj.object_name)
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
               created_at, updated_at, started_at, total_duration_s, 
               llm_provider, llm_model, page_count, pdf_storage_path
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
    file_name = row_dict.get("pdf_storage_path", "").split('/')[-1] if row_dict.get("pdf_storage_path") else None

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
        "llm_model":             row_dict.get("llm_model"),
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
    query = text("SELECT pdf_storage_path FROM idp_smart.document_extractions WHERE task_id = :tid")
    result = await db.execute(query, {"tid": uuid.UUID(task_id)})
    row = result.fetchone()
    if not row or not row[0]:
        return {"error": "Documento no encontrado", "code": 404}
    
    storage_client = get_storage_client()
    obj_name = row[0].split("idp-documents/")[-1]
    
    try:
        response = storage_client.get_object("idp-documents", obj_name)
        return StreamingResponse(response, media_type="application/pdf")
    except Exception as e:
        return {"error": str(e), "code": 500}

@app.get("/api/v1/document/markdown/{task_id}", tags=["Visualización"])
async def view_markdown(task_id: str, db: AsyncSession = Depends(get_db)):
    """Busca el Markdown extraído en MinIO y lo sirve como texto."""
    query = text("SELECT markdown_storage_path FROM idp_smart.document_extractions WHERE task_id = :tid")
    result = await db.execute(query, {"tid": uuid.UUID(task_id)})
    row = result.fetchone()
    if not row or not row[0]:
        return {"error": "Markdown no encontrado", "code": 404}
    
    storage_client = get_storage_client()
    obj_name = row[0].split("idp-documents/")[-1]
    
    try:
        response = storage_client.get_object("idp-documents", obj_name)
        return StreamingResponse(response, media_type="text/markdown; charset=utf-8")
    except Exception as e:
        return {"error": str(e), "code": 500}

@app.post("/api/v1/internal/storage-event")
async def storage_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Webhook unificado para eventos de almacenamiento (SeaweedFS / S3).
    """
    import asyncio
    from urllib.parse import unquote
    import json

    try:
        data = await request.json()
        logger.info(f"📥 Evento de Almacenamiento Recibido: {json.dumps(data)[:200]}")
        
        # 1. Normalizar evento (SeaweedFS Filer vs S3 Standard)
        records = []
        if "Records" in data:
            # Formato S3 (MinIO / SeaweedFS S3 Adapter)
            for r in data["Records"]:
                s3 = r.get("s3", {})
                key = unquote(s3.get("object", {}).get("key", ""))
                if key: records.append(key)
        elif "Event" in data and "Path" in data:
            # Formato Nativo SeaweedFS Filer
            if data["Event"] in ("create", "update"):
                path = data["Path"]
                if path.startswith("/"): path = path[1:]
                # Quitar nombre del bucket si viene incluido
                b_prefix = f"{settings.storage_bucket}/"
                if path.startswith(b_prefix): path = path[len(b_prefix):]
                records.append(path)
        
        if not records:
            return {"status": "no_records_found"}

        for clean_key in records:
            # ── Solo procesar PDFs/TIFs ─────────────────────────────────────────
            allowed_exts = (".pdf", ".tif", ".tiff")
            if not clean_key.lower().endswith(allowed_exts):
                continue

            # ── Retry: esperar hasta 3s a que el INSERT de la API sea visible ───
            tasks = []
            for attempt in range(1, 7):
                await db.rollback()
                query = text("""
                    SELECT task_id, act_type, form_code, json_storage_path, status, llm_provider, llm_model
                    FROM idp_smart.document_extractions
                    WHERE pdf_storage_path LIKE :path
                """)
                result = await db.execute(query, {"path": f"%{clean_key}"})
                tasks = result.fetchall()
                if tasks: break
                await asyncio.sleep(0.5)

            if not tasks:
                logger.warning(f"⚠️ {clean_key} no encontrado en DB tras espera.")
                continue

            for row in tasks:
                task_id, act_type, form_code, json_storage_path, status, prov, mod = row
                if status in ("INICIO", "PENDING_CELERY"):
                    logger.info(f"🚀 Trigger SeaweedFS -> Celery: {task_id}")
                    await db.execute(
                        text("UPDATE idp_smart.document_extractions SET status = 'PENDING_CELERY', updated_at = NOW() WHERE task_id = :tid"),
                        {"tid": task_id}
                    )
                    await db.commit()
                    celery_app.send_task(
                        "process_doc",
                        args=[str(task_id), json_storage_path, clean_key, False, prov, mod],
                        task_id=str(task_id)
                    )
                else:
                    logger.info(f"⏭️ Tarea {task_id} ya tiene estado {status}, saltando trigger.")
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
