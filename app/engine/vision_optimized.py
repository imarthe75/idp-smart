import math
import tempfile
import time
import os
import uuid
import logging
import gc
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pypdf import PdfReader, PdfWriter
import io
import base64

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend

from core.config import settings

import threading
import fcntl

logger = logging.getLogger(__name__)
_INIT_LOCK_FILE = "/tmp/idp_ocr_init.lock"
_global_converter = None
_global_lock = threading.Lock()

class DoclingVisionOptimized:
    def __init__(self):
        """
        Inicia el motor Docling con soporte para saneamiento de memoria y ARQUITECTURA ULTRA-FAST.
        Optimizado para servidores de 48 núcleos con 48GB RAM.
        """
        self.minio_client = None
        from engine.hardware_detector import detect_hardware
        self.profile = detect_hardware()
        
    def _get_pipeline_config(self):
        """Genera la configuración del pipeline para cada instancia de procesamiento."""
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True
        pipeline_options.ocr_options.use_gpu = False
        # Forzamos hilos internos de cada instancia a ser bajos para no saturar
        # Si tenemos 2 batches en 6 cores, cada uno puede usar 2-3 hilos.
        return pipeline_options

    def _get_converter(self):
        """Obtiene o crea el convertidor Global (Singleton por proceso) de forma segura."""
        global _global_converter
        if _global_converter is None:
            with _global_lock:
                if _global_converter is None:
                    pipeline_options = self._get_pipeline_config()
                    # Bloqueo de archivo para evitar que múltiples workers inicialicen a la vez
                    lock_file = open(_INIT_LOCK_FILE, "w")
                    try:
                        fcntl.flock(lock_file, fcntl.LOCK_EX)
                        logger.info("🛠️ [OCR] Inicializando DocumentConverter (Singleton)...")
                        conv = DocumentConverter(
                            allowed_formats=[InputFormat.PDF],
                            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options, backend=PyPdfiumDocumentBackend)}
                        )
                        # WARMUP: Realizar una conversión mínima para forzar la carga/unzip de modelos ocr
                        # Esto evita que 'convert' dispare descargas concurrentes fuera del lock.
                        try:
                            logger.info("🔥 [OCR] Warmup de modelos...")
                            with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
                                writer = PdfWriter()
                                writer.add_blank_page(width=72, height=72)
                                writer.write(tmp.name)
                                conv.convert(tmp.name)
                        except Exception as e_warm:
                            logger.warning(f"⚠️ Warmup fallido (ignorable): {e_warm}")
                        
                        _global_converter = conv
                        logger.info("✅ [OCR] DocumentConverter listo y modelos cargados.")
                    finally:
                        fcntl.flock(lock_file, fcntl.LOCK_UN)
                        lock_file.close()
        return _global_converter

    def _process_chunk_with_runpod(self, file_path: str) -> str:
        """ENVÍA UN CHUNK A RUNPOD DOCLING (GPU)"""
        if not settings.runpod_docling_url:
            return ""
        
        try:
            logger.info("☁️ [RUNPOD] Enviando lote a RunPod Docling...")
            with open(file_path, "rb") as f:
                content = f.read()
            
            # Formato estándar de RunPod Docling API
            payload = {
                "input": {
                    "pdf_base64": base64.b64encode(content).decode('utf-8'),
                    "options": {
                        "do_ocr": True,
                        "do_table_structure": True
                    }
                }
            }
            
            headers = {"Authorization": f"Bearer {settings.runpod_api_key}"}
            resp = requests.post(
                settings.runpod_docling_url, 
                json=payload, 
                timeout=settings.docling_runpod_timeout,
                headers=headers
            )
            resp.raise_for_status()
            
            # Suponiendo que la respuesta viene en "output" -> "markdown"
            data = resp.json()
            if "output" in data and "markdown" in data["output"]:
                return data["output"]["markdown"]
            elif "output" in data and isinstance(data["output"], str):
                return data["output"] # Caso simple
            
            logger.warning("⚠️ RunPod respondió pero no incluyó markdown válido.")
            return ""
            
        except Exception as e:
            logger.warning(f"⚠️ Fallo en RunPod Docling: {e}. Activando fallback local...")
            return ""

    def _process_chunk_with_timeout(self, file_path: str, timeout: int = 300) -> str:
        """Procesa un chunk priorizando RunPod, luego Local."""
        # 1. Intentar RunPod si está configurado
        if settings.runpod_docling_url:
            md_runpod = self._process_chunk_with_runpod(file_path)
            if md_runpod:
                return md_runpod
        
        # 2. Fallback Local
        try:
            converter = self._get_converter()
            result = converter.convert(file_path)
            return result.document.export_to_markdown()
        except Exception as e:
            logger.error(f"❌ Error convirtiendo lote local: {e}")
            raise RuntimeError(f"Fallo en lote OCR (Local Fallback): {e}")

    def extract_markdown_from_minio_sync(self, object_name: str) -> tuple[str, int, str]:
        """
        PARALLEL-BATCH: 2 lotes simultáneos.
        Optimizado para workers de 6 núcleos (3 núcleos por lote).
        """
        from core.minio_client import get_minio_client
        self.minio_client = get_minio_client()
        
        temp_dir = tempfile.gettempdir()
        base_name = os.path.basename(object_name)
        local_path = os.path.join(temp_dir, f"full_{uuid.uuid4()}_{base_name}")
        
        try:
            self.minio_client.fget_object(settings.minio_bucket, object_name, local_path)
            reader = PdfReader(local_path)
            total_pages = len(reader.pages)
            
            # ESTRATEGIA BALANCEADA: Ajustada para 6 núcleos y 6GB RAM
            # Aumentamos chunk a 10 para reducir fragmentación
            chunk_size = max(10, self.profile.pdf_chunk_size)
            # En CPU con 6 cores, 1 batch grande suele ser mejor que 2 pequeños que re-re-procesan
            max_parallel_batches = 1 if self.profile.cpu_cores <= 8 else 2
            
            strategy = f"Fast-{chunk_size}x{max_parallel_batches}"
            logger.info(f"🚀 [ESTRATEGIA] {strategy} | Doc: {object_name} | Págs: {total_pages}")
            
            segments = []
            for start in range(0, total_pages, chunk_size):
                end = min(start + chunk_size, total_pages)
                segments.append((start, end))

            results_map = {}

            def process_segment(start, end, idx):
                inner_reader = PdfReader(local_path)
                writer = PdfWriter()
                for p_idx in range(start, end): writer.add_page(inner_reader.pages[p_idx])
                
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_chunk:
                    chunk_path = tmp_chunk.name
                    writer.write(chunk_path)
                
                try:
                    # 120s por página de timeout
                    md_part = self._process_chunk_with_timeout(chunk_path, timeout=(end-start)*120)
                    logger.info(f"✅ Lote {idx} finalizado ({start+1}-{end}).")
                    return idx, md_part
                finally:
                    if os.path.exists(chunk_path): os.unlink(chunk_path)
                    gc.collect()

            # Paralelismo controlado
            with ThreadPoolExecutor(max_workers=max_parallel_batches) as executor:
                future_to_idx = {executor.submit(process_segment, s, e, i): i for i, (s, e) in enumerate(segments)}
                for future in as_completed(future_to_idx):
                    idx, md_part = future.result()
                    results_map[idx] = md_part

            full_markdown = "\n\n".join(results_map[i] for i in range(len(segments)))
            gc.collect()
            
            return full_markdown, total_pages, strategy

        except Exception as e:
            logger.error(f"❌ Error crítico en Visión Ultra-Fast: {e}")
            return "", 0, "ERROR"
            
        finally:
            if os.path.exists(local_path): os.unlink(local_path)

    def extract_visual_analysis_sync(self, pdf_path: str) -> str:
        """... (esta parte se mantiene igual) ..."""
        logger.info("👁️ [VISION] Iniciando análisis visual...")
        base_url = settings.runpod_vision_url or "http://localhost:8001"
        if not base_url.startswith("http"): base_url = f"http://{base_url}"
        prompt = "Describe de forma técnica: 1) Firmas, 2) Sellos, 3) Hologramas, 4) Logotipos."
        try:
            import base64, fitz
            doc = fitz.open(pdf_path)
            img_data = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
            doc.close()
            payload = {"model": "Qwen/Qwen2-VL-7B-Instruct-AWQ", "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(img_data).decode('utf-8')}"}}]}], "temperature": 0.1, "max_tokens": 1024}
            resp = requests.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=payload, timeout=90)
            analysis = resp.json()["choices"][0]["message"]["content"]
            return f"\n\n### ANÁLISIS VISUAL (Qwen2-VL)\n{analysis}\n"
        except Exception: return ""

vision_engine = DoclingVisionOptimized()

def extract_markdown_from_minio_sync(object_name: str) -> tuple[str, int, str]:
    return vision_engine.extract_markdown_from_minio_sync(object_name)

def extract_visual_analysis_sync(pdf_path: str) -> str:
    return vision_engine.extract_visual_analysis_sync(pdf_path)
