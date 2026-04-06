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
        Inicia el motor Docling con soporte para saneamiento de memoria y ARQUITECTURA AUTOADAPTABLE.
        Optimización dinámica de recursos según la clase HardwareProfile.
        """
        self.minio_client = None
        from engine.hardware_detector import detect_hardware
        self.profile = detect_hardware()
        
    def _get_pipeline_config(self):
        """Genera la configuración del pipeline para cada instancia de procesamiento."""
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True
        
        # Docling 2.x API: Usar accelerator_options en lugar de ocr_options.use_gpu
        pipeline_options.accelerator_options.device = "cpu"
        # ── AUTO-ADAPTACIÓN DINÁMICA ──────────────────────────────────────────
        # Dividimos los núcleos disponibles (cpuset) entre los lotes paralelos
        # para que cada instancia de Docling use exactamente su porción de CPU.
        num_threads = max(1, self.profile.cpu_cores // self.profile.max_parallel_batches)
        pipeline_options.accelerator_options.num_threads = num_threads
        
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

    def extract_markdown_cloud(self, pdf_path: str, engine: str) -> str:
        """Soporte multinivel para OCR Cloud (AWS, GCP, Azure)."""
        logger.info(f"☁️ [VISION] Iniciando extracción Cloud via {engine}...")
        if engine == "google_doc_ai": return "# GOOGLE DOCUMENT AI (Plumbing Ready - Config credentials in .env)"
        if engine == "aws_textract": return "# AWS TEXTRACT (Plumbing Ready - Config credentials in .env)"
        if engine == "azure_ai_vision": return "# AZURE AI VISION (Plumbing Ready - Config credentials in .env)"
        return ""

    def extract_markdown_from_minio_sync(self, object_name: str) -> tuple[str, int, str]:
        """[PASO 1: VISIÓN] Obtiene el documento y extrae texto (Docling o Cloud)."""
        # --- Selector de Motor Masivo ---
        engine_type = getattr(settings, "ocr_engine", "docling").lower()
        
        # Docling (Local)
        if engine_type == "docling":
            return self._extract_markdown_local(object_name)
            
        # Cloud Engines (AWS, GCP, Azure)
        local_path = self._download_from_minio(object_name)
        try:
            markdown = self.extract_markdown_cloud(local_path, engine_type)
            from pypdf import PdfReader
            reader = PdfReader(local_path)
            return markdown, len(reader.pages), engine_type.upper()
        finally:
            if os.path.exists(local_path): os.unlink(local_path)

    def _download_from_minio(self, object_name: str) -> str:
        from core.minio_client import get_minio_client
        client = get_minio_client()
        tmp_p = os.path.join(tempfile.gettempdir(), f"full_{uuid.uuid4()}.pdf")
        client.fget_object(settings.minio_bucket, object_name, tmp_p)
        return tmp_p

    def _extract_markdown_local(self, object_name: str) -> tuple[str, int, str]:
        """Lógica robusta de Docling con segmentación óptima para 12-hilos."""
        local_path = self._download_from_minio(object_name)
        try:
            results_map = {}
            from pypdf import PdfReader
            reader = PdfReader(local_path)
            total_pages = len(reader.pages)
            
            # --- SEGMENTACIÓN BALANCEADA (Serial-Power) ---
            # Segmentos más grandes (15 págs) reducen el overhead de inicialización
            effective_chunk = 15 if total_pages > 15 else 5
            max_parallel_batches = self.profile.max_parallel_batches # Ahora es 1!
            
            strategy = f"Serial-Power-{effective_chunk}x{max_parallel_batches}"
            logger.info(f"🚀 [ESTRATEGIA] {strategy} | Doc: {object_name} | Págs: {total_pages}")
            
            segments = []
            for start in range(0, total_pages, effective_chunk):
                end = min(start + effective_chunk, total_pages)
                segments.append((start, end))

            with ThreadPoolExecutor(max_workers=max_parallel_batches) as executor:
                # El proceso es serial por worker, pero rápido por los 12 hilos asignados
                for idx, (s, e) in enumerate(segments):
                    chunk_p = os.path.join(tempfile.gettempdir(), f"chunk_{idx}_{uuid.uuid4()}.pdf")
                    try:
                        from pypdf import PdfWriter
                        writer = PdfWriter()
                        # Reutilizar el reader para no reabrir el archivo 100 veces
                        for p_idx in range(s, e): writer.add_page(reader.pages[p_idx])
                        with open(chunk_p, "wb") as f_out: writer.write(f_out)
                        
                        md_part = self._process_chunk_with_timeout(chunk_p, timeout=(e-s)*120)
                        results_map[idx] = md_part
                        logger.info(f"✅ Segmento {idx} ({s+1}-{e}) completado.")
                    except Exception as e_seg:
                        logger.error(f"⚠️ Error en segmento {idx}: {e_seg}")
                    finally:
                        if os.path.exists(chunk_p): os.unlink(chunk_p)

            if not results_map: raise ValueError("Fallo total en Docling.")
            full_markdown = "\n\n".join(results_map[i] for i in sorted(results_map.keys()))
            return full_markdown, total_pages, strategy

        except Exception as e:
            logger.error(f"❌ Error crítico en Visión: {e}")
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
