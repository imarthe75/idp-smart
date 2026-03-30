from sqlalchemy import Column, Integer, String, Text, DateTime, Float, func, UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class DocumentExtraction(Base):
    """
    Tabla principal de extracción de documentos.
    Almacena el JSON completo del sistema Java (extracted_data),
    un JSON simplificado solo con label/value (simplified_json),
    y la ruta al markdown generado por Docling (markdown_minio_path).
    """
    __tablename__ = "document_extractions"
    __table_args__ = {"schema": "idp_smart"}

    id                  = Column(Integer, primary_key=True, index=True)
    task_id             = Column(UUID(as_uuid=True), unique=True, index=True, nullable=False)
    act_type            = Column(String(255), nullable=True)   # dsactocorta, ej: BI34
    expediente_id       = Column(String(255), nullable=True)   # ID del expediente o nombre de archivo
    form_code           = Column(String(255), nullable=True)   # lldeffrmpre de cfdeffrmpre
    pdf_minio_path      = Column(String(1024), nullable=True)  # ruta del documento en MinIO
    json_minio_path     = Column(String(1024), nullable=True)  # ruta del esquema JSON en MinIO
    markdown_minio_path = Column(String(1024), nullable=True)  # ruta del markdown generado por Docling
    additional_docs     = Column(JSONB, nullable=True)  # Lista de rutas MinIO para documentos adicionales
    parent_task_id      = Column(UUID(as_uuid=True), nullable=True)    # Referencia si se rehusó el markdown de otra tarea
    stage_current       = Column(String(100), nullable=True)   # etapa activa en el worker
    status              = Column(String(50), default="PENDING_CELERY")
    extracted_data      = Column(JSONB, nullable=True)  # JSON completo
    simplified_json     = Column(JSONB, nullable=True)  # JSON reducido
    
    # --- Métricas de rendimiento y metadatos ---
    page_count          = Column(Integer, default=0)
    llm_provider        = Column(String(50), nullable=True)
    llm_model           = Column(String(100), nullable=True)
    gpu_model           = Column(String(100), nullable=True)
    docling_duration_s  = Column(Float, nullable=True)
    ai_duration_s       = Column(Float, nullable=True)
    total_duration_s    = Column(Float, nullable=True)
    error_message       = Column(Text, nullable=True)

    started_at          = Column(DateTime(timezone=True), nullable=True) # Fecha real de inicio
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), onupdate=func.now())


class ProcessLog(Base):
    """
    Tabla de log de ejecuciones del motor idp-smart.
    Registra cada evento relevante de la extracción (inicio, etapas, errores, duración).
    """
    __tablename__ = "process_logs"
    __table_args__ = {"schema": "idp_smart"}

    id           = Column(Integer, primary_key=True, index=True)
    task_id      = Column(UUID(as_uuid=True), index=True, nullable=False)  # Referencia a document_extractions
    stage        = Column(String(100), nullable=False)              # Etapa: VISION | SCHEMA_LOAD | AGENT | MAPPER | DB_SAVE | ERROR
    level        = Column(String(20), default="INFO")               # INFO | WARNING | ERROR | DEBUG
    message      = Column(Text, nullable=False)                     # Mensaje descriptivo del evento
    detail       = Column(JSONB, nullable=True)                      # Datos adicionales (métricas, errores, payloads cortos)
    duration_ms  = Column(Float, nullable=True)                     # Duración de la etapa en milisegundos
    created_at   = Column(DateTime(timezone=True), server_default=func.now())


class ActFormsCatalog(Base):
    """
    Catálogo de actos y formas pre-codificadas.
    """
    __tablename__ = "act_forms_catalog"
    __table_args__ = {"schema": "idp_smart"}

    id           = Column(Integer, primary_key=True, index=True)
    form_code    = Column(String(50))
    lldeffrmpre  = Column(Integer)
    llacto       = Column(Integer)
    dsactocorta  = Column(String(100))
    dsacto       = Column(String(255))
    jsconfforma  = Column(JSONB)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
