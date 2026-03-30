# Use official Python 3.11 slim image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PYTHONPATH=/app

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create and set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-descarga de modelos Docling y EasyOCR para evitar race conditions (Errno 2)
# Esto descarga los pesos del modelo de Layout y OCR durante el build de la imagen.
RUN python3 -c "from docling.document_converter import DocumentConverter; \
    from docling.datamodel.base_models import InputFormat; \
    from docling.datamodel.pipeline_options import PdfPipelineOptions; \
    from docling.document_converter import PdfFormatOption; \
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend; \
    pipeline_options = PdfPipelineOptions(); \
    pipeline_options.do_ocr = True; \
    DocumentConverter(allowed_formats=[InputFormat.PDF], format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options, backend=PyPdfiumDocumentBackend)})"

# Copy project files
COPY ./app /app/

# Expose port for FastAPI
EXPOSE 8000
