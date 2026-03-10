from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, func
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class DocumentExtraction(Base):
    __tablename__ = "document_extractions"
    __table_args__ = {"schema": "idp_smart"}
    
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(255), unique=True, index=True, nullable=False)
    act_type = Column(String(255), nullable=True) # e.g. "Escritura", "Acta"
    form_code = Column(String(255), nullable=True) # reference to cfdeffrmpre id
    pdf_minio_path = Column(String(1024), nullable=True) # path where doc lives in MinIO
    extracted_data = Column(JSON, nullable=True) # the final JSON mapped data
    status = Column(String(50), default="PROCESSING")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

