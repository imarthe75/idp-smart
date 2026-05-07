"""
OCR Engine Factory — idp-smart
--------------------------------
Provider-agnostic OCR abstraction.
Controlado por la variable de entorno OCR_ENGINE:
  - docling       → Motor local. Si hay GPU usa CUDA; si no, CPU con chunking.
  - google_doc_ai → Google Document AI (cloud).
  - aws_textract  → AWS Textract (cloud).

Todos los motores devuelven una cadena Markdown estandarizada
y el número de páginas procesadas.

Modo CPU (Docling):
  - Chunking automático: procesa DOCLING_CHUNK_SIZE páginas a la vez.
  - Límites OMP/MKL aplicados vía hardware_detector.
  - Etiqueta processing_unit='CPU' o 'GPU:<nombre>' para benchmarks.
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Tuple, Union

logger = logging.getLogger(__name__)

DocumentInput = Union[str, Path, bytes]
# Retorna (markdown: str, num_pages: int, processing_unit: str)
OCRResult = Tuple[str, int, str]


# ===========================================================================
# Motor BASE
# ===========================================================================
class BaseOCREngine:
    def extract_markdown(self, document: DocumentInput) -> OCRResult:
        raise NotImplementedError


# ===========================================================================
# Motor DOCLING (local — CPU o GPU según hardware)
# ===========================================================================
class DoclingEngine(BaseOCREngine):
    """
    Motor local de extracción de documentos con soporte para:
      - GPU (CUDA): procesamiento completo sin chunking.
      - CPU: procesamiento por chunks de N páginas para evitar OOM de RAM.

    La detección de hardware es automática vía hardware_detector.
    """

    def __init__(self):
        # Detectar hardware y aplicar límites de threads ANTES de importar torch/docling
        from engine.hardware_detector import detect_hardware, apply_thread_limits

        self._hw = detect_hardware()
        apply_thread_limits(self._hw)

        logger.info(
            "DoclingEngine iniciando: device=%s | chunk=%d páginas | unit=%s",
            self._hw.docling_device,
            self._hw.pdf_chunk_size,
            self._hw.processing_unit,
        )

        # Configurar pipeline de Docling
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions, EasyOcrOptions
        from docling.datamodel.base_models import InputFormat

        pipeline_opts = PdfPipelineOptions()
        pipeline_opts.do_ocr = True
        pipeline_opts.do_table_structure = True
        pipeline_opts.ocr_options = EasyOcrOptions(lang=["es"])

        # Forzar device según hardware detectado
        if hasattr(pipeline_opts, "accelerator_options"):
            from docling.datamodel.pipeline_options import AcceleratorOptions, AcceleratorDevice
            device = (
                AcceleratorDevice.CUDA
                if self._hw.has_gpu
                else AcceleratorDevice.CPU
            )
            pipeline_opts.accelerator_options = AcceleratorOptions(device=device)
        else:
            # Versiones antiguas de docling: control por variable de entorno
            os.environ["DOCLING_DEVICE"] = self._hw.docling_device

        self._converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts)
            }
        )
        self._chunk_size = self._hw.pdf_chunk_size

    # ── Extracción completa (un archivo) ───────────────────────────────────────
    def extract_markdown(self, document: DocumentInput) -> OCRResult:
        # Convertir a bytes para poder contar páginas y hacer chunking
        raw_bytes = self._to_bytes(document)

        page_count = self._count_pages(raw_bytes)
        logger.info(
            "Docling: %d páginas detectadas. chunk_size=%d. device=%s",
            page_count,
            self._chunk_size,
            self._hw.docling_device,
        )

        # Sin chunking si estamos en GPU o el PDF es pequeño
        if self._hw.has_gpu or page_count <= self._chunk_size:
            md = self._convert_bytes(raw_bytes)
            return md, page_count, self._hw.processing_unit

        # Chunking en CPU para evitar OOM de RAM
        return self._extract_chunked(raw_bytes, page_count)

    # ── Chunking CPU ───────────────────────────────────────────────────────────
    def _extract_chunked(self, raw_bytes: bytes, total_pages: int) -> OCRResult:
        """Procesa el PDF en trozos de self._chunk_size páginas."""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.warning("PyMuPDF no instalado; procesando sin chunking.")
            md = self._convert_bytes(raw_bytes)
            return md, total_pages, self._hw.processing_unit

        src_doc = fitz.open(stream=raw_bytes)
        chunks_md: list[str] = []
        processed = 0

        for start in range(0, total_pages, self._chunk_size):
            end = min(start + self._chunk_size, total_pages)
            logger.info("Docling CPU chunk: páginas %d–%d / %d", start + 1, end, total_pages)

            # Extraer sub-documento
            chunk_doc = fitz.open()
            chunk_doc.insert_pdf(src_doc, from_page=start, to_page=end - 1)

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                chunk_doc.save(tmp.name)
                chunk_path = tmp.name
            chunk_doc.close()

            try:
                chunk_md = self._convert_bytes(Path(chunk_path).read_bytes())
                chunks_md.append(chunk_md)
                processed += (end - start)
            except Exception as exc:
                logger.error("Error en chunk %d–%d: %s", start + 1, end, exc)
                chunks_md.append(
                    f"\n\n<!-- Error en páginas {start+1}-{end}: {exc} -->\n\n"
                )
            finally:
                os.unlink(chunk_path)

        src_doc.close()
        full_md = "\n\n".join(chunks_md)
        logger.info(
            "Docling CPU completado: %d páginas, %d chars",
            processed,
            len(full_md),
        )
        return full_md, total_pages, self._hw.processing_unit

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _convert_bytes(self, raw: bytes) -> str:
        from docling.datamodel.base_models import DocumentStream

        stream = DocumentStream(name="document.pdf", stream=io.BytesIO(raw))
        result = self._converter.convert(stream)
        return result.document.export_to_markdown()

    @staticmethod
    def _to_bytes(document: DocumentInput) -> bytes:
        if isinstance(document, bytes):
            return document
        return Path(document).read_bytes()

    @staticmethod
    def _count_pages(raw: bytes) -> int:
        try:
            import fitz
            doc = fitz.open(stream=raw)
            n = len(doc)
            doc.close()
            return n
        except Exception:
            return 1


# ===========================================================================
# Motor GOOGLE DOCUMENT AI (cloud)
# ===========================================================================
class GoogleDocAIEngine(BaseOCREngine):
    """
    Usa Google Document AI para extracción en la nube.
    """

    def __init__(self):
        from google.cloud import documentai
        
        # Validar configuración
        if not settings.google_docai_project_id or not settings.google_docai_processor_id:
            logger.warning("⚠️ Google DocAI mal configurado. Revisa GOOGLE_DOCAI_PROJECT_ID.")
            raise ValueError("Configuración de Google DocAI incompleta.")

        self._project_id = settings.google_docai_project_id
        self._location = settings.google_docai_location or "us"
        self._processor_id = settings.google_docai_processor_id
        self._client = documentai.DocumentProcessorServiceClient()
        self._processor_name = self._client.processor_path(
            self._project_id, self._location, self._processor_id
        )

    def extract_markdown(self, document: DocumentInput) -> OCRResult:
        from google.cloud import documentai

        raw = document if isinstance(document, bytes) else Path(document).read_bytes()
        raw_doc = documentai.RawDocument(content=raw, mime_type="application/pdf")
        request = documentai.ProcessRequest(
            name=self._processor_name, raw_document=raw_doc
        )
        result = self._client.process_document(request=request)
        doc = result.document
        
        # Extracción estructurada básica
        paragraphs = [
            block.layout.text_anchor.content
            for page in doc.pages
            for block in page.blocks
        ] if doc.pages else []
        
        md = "\n\n".join(paragraphs) if paragraphs else doc.text
        pages = len(doc.pages) if doc.pages else 1
        logger.info("✅ Google DocAI: %d páginas procesadas.", pages)
        return md, pages, "CLOUD:GoogleDocAI"


# ===========================================================================
# Motor AWS TEXTRACT (cloud)
# ===========================================================================
class AWSTextractEngine(BaseOCREngine):
    """
    Usa AWS Textract para extracción en la nube.
    """

    def __init__(self):
        import boto3
        
        if not settings.aws_access_key_id or not settings.aws_secret_access_key:
            logger.warning("⚠️ AWS Credentials faltantes para Textract.")
            raise ValueError("Configuración de AWS incompleta.")

        self._client = boto3.client(
            "textract",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region or "us-east-1"
        )

    def extract_markdown(self, document: DocumentInput) -> OCRResult:
        raw = document if isinstance(document, bytes) else Path(document).read_bytes()
        
        # DetectDocumentText solo soporta una página si se pasa por Bytes directamente
        # Para multipágina se requiere S3, pero aquí simulamos flujo síncrono
        try:
            response = self._client.detect_document_text(Document={"Bytes": raw})
            lines = [
                block["Text"]
                for block in response.get("Blocks", [])
                if block["BlockType"] == "LINE"
            ]
            md = "\n\n".join(lines)
            logger.info("✅ AWS Textract procesado exitosamente (Single-Page Sync Mode).")
            return md, 1, "CLOUD:AWSTextract"
        except Exception as e:
            logger.error(f"❌ Error en AWS Textract: {e}")
            raise


# ===========================================================================
# Motor AZURE DOCUMENT INTELLIGENCE (cloud)
# ===========================================================================
class AzureDocAIEngine(BaseOCREngine):
    """
    Usa Azure Document Intelligence (Layout model) para extracción con tablas.
    """

    def __init__(self):
        from azure.ai.formrecognizer import DocumentAnalysisClient
        from azure.core.credentials import AzureKeyCredential

        if not settings.azure_doc_endpoint or not settings.azure_doc_key:
            logger.warning("⚠️ Azure Endpoint o Key faltantes.")
            raise ValueError("Configuración de Azure incompleta.")

        self._client = DocumentAnalysisClient(
            endpoint=settings.azure_doc_endpoint,
            credential=AzureKeyCredential(settings.azure_doc_key)
        )

    def extract_markdown(self, document: DocumentInput) -> OCRResult:
        raw = document if isinstance(document, bytes) else Path(document).read_bytes()
        
        poller = self._client.begin_analyze_document("prebuilt-layout", raw)
        result = poller.result()

        md_lines = []
        for page in result.pages:
            for line in page.lines:
                md_lines.append(line.content)
        
        # Tablas (Opcional: Agregar lógica de conversión a Markdown Table si es crítico)
        # Por ahora extraemos el texto plano de forma robusta
        md = "\n\n".join(md_lines)
        pages = len(result.pages)
        logger.info("✅ Azure DocAI: %d páginas procesadas.", pages)
        return md, pages, "CLOUD:AzureDocAI"


# ===========================================================================
# FACTORY
# ===========================================================================
_ENGINE_MAP = {
    "docling":       DoclingEngine,
    "google":        GoogleDocAIEngine,
    "google_doc_ai": GoogleDocAIEngine,
    "aws":           AWSTextractEngine,
    "aws_textract":  AWSTextractEngine,
    "azure":         AzureDocAIEngine,
    "azure_doc_ai":  AzureDocAIEngine,
}


def get_ocr_engine(engine_name: str | None = None) -> BaseOCREngine:
    """
    Retorna una instancia del motor OCR configurado con fallback automático.
    """
    name = (engine_name or settings.ocr_engine or "docling").lower()
    engine_cls = _ENGINE_MAP.get(name)
    
    if engine_cls is None:
        logger.warning(f"⚠️ Motor OCR '{name}' no reconocido. Fallback a Docling.")
        return DoclingEngine()
        
    try:
        logger.info("🚀 Inicializando motor OCR: %s", name)
        return engine_cls()
    except Exception as exc:
        logger.error("❌ Fallo Crítico inicializando '%s': %s — Usando Docling local.", name, exc)
        return DoclingEngine()
