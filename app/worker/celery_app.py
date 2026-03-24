from celery import Celery
from core.config import settings
from core.idp_logger import log_event, timed_stage, build_simplified_json
from core.minio_client import get_minio_client, upload_file_to_minio
from sqlalchemy import create_engine, text
import json
import traceback
import uuid

# Import Engine Components
from engine.vision_optimized import extract_markdown_from_minio_sync as extract_markdown_from_minio
from engine.agent import extract_form_data
from engine.mapper import get_json_schema, map_results_to_json, extract_fields_from_schema

# Create celery application
celery_app = Celery(
    "idp_worker",
    broker=settings.valkey_url,
    backend=settings.valkey_url
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='America/Mexico_City',
    enable_utc=True,
    worker_concurrency=1
)

# Sync engine for Celery
sync_database_url = settings.database_url.replace("postgresql+asyncpg", "postgresql")
db_engine = create_engine(sync_database_url)

# Etapas definidas del proceso (usadas para cálculo de progreso en frontend)
STAGES = ["INICIO", "VISION", "SCHEMA_LOAD", "AGENT", "MAPPER", "SIMPLIFY", "DB_SAVE", "ERROR"]


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
                        SET stage_current = :stage, updated_at = NOW()
                        WHERE task_id = :task_id
                    """),
                    {"stage": stage, "task_id": uuid.UUID(str(task_id))},
                )
    except Exception as exc:
        log_event(db_engine, task_id, "SYSTEM", f"No se pudo actualizar stage_current a {stage}: {exc}", level="WARNING")


@celery_app.task(name="process_doc")
def process_doc(task_id: str, json_minio_object: str, pdf_minio_object: str, skip_vision: bool = False):
    """
    Pipeline de extracción semántica de documentos:
    INICIO → VISION → SCHEMA_LOAD → AGENT → MAPPER → SIMPLIFY → DB_SAVE
    
    Si skip_vision=True o ya existe un markdown_minio_path, se salta la etapa VISION.
    """
    minio_client = get_minio_client()
    doc_markdown = None

    try:
        # ── INICIO ───────────────────────────────────────────────────────────────
        with db_engine.begin() as conn:
            conn.execute(
                text("UPDATE idp_smart.document_extractions SET started_at = NOW(), stage_current = 'INICIO' WHERE task_id = :tid"),
                {"tid": uuid.UUID(str(task_id))}
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

        # Procesar Documento Principal
        import time
        docling_start = time.time()
        docling_duration = 0.0
        
        if not skip_vision:
            _set_stage(task_id, "VISION")
            with timed_stage(db_engine, task_id, "VISION", "Extracción Markdown — Documento Principal"):
                log_event(db_engine, task_id, "VISION", "[OPTIMIZADO] Docling: detección scaneado + paralelismo + cache Redis")
                doc_markdown = extract_markdown_from_minio(pdf_minio_object)
                if not doc_markdown:
                    doc_markdown = "# Documento Principal\nContenido no extraído."
                    log_event(db_engine, task_id, "VISION",
                              "Docling no extrajo contenido, usando fallback de texto vacío.", level="WARNING")
                else:
                    log_event(db_engine, task_id, "VISION",
                              f"Markdown generado: {len(doc_markdown)} caracteres extraídos.",
                              detail={"char_count": len(doc_markdown), "engine": "vision_optimized"})
        else:
            log_event(db_engine, task_id, "VISION", "Reutilizando markdown principal existente.")

        # Procesar Documentos Adicionales
        if additional_paths:
            with timed_stage(db_engine, task_id, "VISION", f"Extrayendo {len(additional_paths)} documentos adicionales"):
                for i, path in enumerate(additional_paths):
                    try:
                        obj_name = path.split("idp-documents/")[-1]
                        log_event(db_engine, task_id, "VISION", f"Procesando doc adicional {i+1}: {obj_name}")
                        add_md = extract_markdown_from_minio(obj_name)
                        if add_md:
                            header = f"\n\n--- DOCUMENTO ADICIONAL {i+1} ---\n"
                            additional_mds.append(header + add_md)
                    except Exception as e_add:
                        log_event(db_engine, task_id, "VISION", f"Error en doc adicional {i+1}: {e_add}", level="WARNING")
        
        docling_duration = time.time() - docling_start

        # Concatenar todo el contexto
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

        # ── AGENT ─────────────────────────────────────────────────────────────────
        _set_stage(task_id, "AGENT")
        extracted_key_val = None
        ai_start = time.time()
        with timed_stage(db_engine, task_id, "AGENT", "Extracción semántica con LangChain Agent"):
            # Pasamos EL ESQUEMA COMPLETO Y ESTRUCTURADO al agente para que entienda las jerarquías
            extracted_key_val = extract_form_data(doc_markdown, schema)
            
            # Buscamos cuántas llaves (UUIDs del root) llenó
            filled_count = sum(1 for v in extracted_key_val.values() if v) if extracted_key_val else 0
            total_count = len(flat_fields)
            
            log_event(db_engine, task_id, "AGENT",
                      f"Agente retornó {len(extracted_key_val) if extracted_key_val else 0} UUIDs raíz (Llenos: {filled_count}).",
                      detail={
                          "filled": filled_count, "total_flat_fields": total_count,
                          "fill_rate": f"{round(filled_count / total_count * 100, 1)}%" if total_count else "N/A"
                      })
        ai_duration = time.time() - ai_start

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

        error_msg = f"Error en etapa {stage_at_error}: {str(e)}"
        print(error_msg)
        traceback.print_exc()
        log_event(db_engine, task_id, "ERROR", error_msg, level="ERROR", detail={"traceback": traceback.format_exc(), "stage": stage_at_error})
        
        # Guardamos un status que identifique dónde tronó
        final_error_status = f"ERROR_{stage_at_error}"
        _set_stage(task_id, stage_at_error, status=final_error_status)
        
        return {"task_id": task_id, "status": "ERROR", "error": error_msg, "stage": stage_at_error}
