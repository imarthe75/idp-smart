"""
🚀 Vision Engine Optimizado para GPU NVIDIA + RunPod
- Detección inteligente de scaneados vs texto
- Procesamiento adaptativo: paralelo en CPU, serial optimizado en GPU
- Batching dinámico basado en memoria disponible
- Cache Redis/Valkey para OCR
- Monitoreo de performance y métricas GPU
- Preparado para GPU NVIDIA + RunPod Pods con fallback inteligente
- Escalable: CPU → GPU → Distribuido (RunPod)
"""

import os
import json
import tempfile
import hashlib
import logging
import psutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, List
import redis
import httpx
import asyncio
from dataclasses import dataclass, asdict
from enum import Enum

from core.config import settings
from core.minio_client import get_minio_client

# Logger
logger = logging.getLogger(__name__)


class ProcessingDevice(Enum):
    """Estrategias de procesamiento según device"""
    CPU_PARALLEL = "cpu_parallel"      # CPU: múltiples threads
    GPU_OPTIMIZED = "gpu_optimized"    # GPU: procesamiento serial optimizado
    RUNPOD_SERVERLESS = "runpod"       # RunPod: distribuido
    HYBRID = "hybrid"                   # Híbrido: RunPod + fallback local


@dataclass
class ProcessingMetrics:
    """Métricas de procesamiento para cada documento"""
    object_name: str
    device_used: str
    pages_processed: int
    time_elapsed: float
    characters_output: int
    cache_hit: bool
    runpod_used: bool
    gpu_memory_used_mb: float = 0.0
    cpu_memory_used_mb: float = 0.0
    throughput_pages_per_sec: float = 0.0
    
    def log(self):
        """Log métrica en formato legible"""
        if self.pages_processed > 0 and self.time_elapsed > 0:
            self.throughput_pages_per_sec = self.pages_processed / self.time_elapsed
            
        logger.info(
            f"📊 METRICS: {self.object_name} | "
            f"Device: {self.device_used} | "
            f"Pages: {self.pages_processed} | "
            f"Time: {self.time_elapsed:.2f}s | "
            f"Output: {self.characters_output} chars | "
            f"Throughput: {self.throughput_pages_per_sec:.2f} pg/s"
        )


class VisionCache:
    """Gestiona cache de OCR en Redis/Valkey con expiración automática"""
    
    def __init__(self):
        """Inicializa conexión a Redis/Valkey"""
        self.enabled = settings.vision_use_cache
        self.ttl = settings.vision_cache_ttl
        self.redis_client = None
        
        if self.enabled:
            try:
                self.redis_client = redis.from_url(
                    settings.valkey_url,
                    decode_responses=True,
                    socket_timeout=5,
                    retry_on_timeout=True
                )
                # Verificar conexión
                self.redis_client.ping()
                logger.info("✅ Cache Redis/Valkey conectado (TTL: 7 días)")
            except Exception as e:
                logger.warning(f"⚠️ Cache deshabilitado: {e}")
                self.enabled = False
    
    def get_cache_key(self, object_name: str, page_num: Optional[int] = None) -> str:
        """Genera clave de cache única para documento"""
        key_part = f"{object_name}:{'full' if page_num is None else page_num}"
        return f"docling:ocr:v4:{hashlib.md5(key_part.encode()).hexdigest()}"
    
    def get(self, object_name: str, page_num: Optional[int] = None) -> Optional[str]:
        """Obtiene OCR del cache (O(1) lookup)"""
        if not self.enabled or not self.redis_client:
            return None
        
        try:
            key = self.get_cache_key(object_name, page_num)
            value = self.redis_client.get(key)
            if value:
                logger.debug(f"💾 [CACHE HIT] {object_name}")
                return value
            return None
        except Exception as e:
            logger.warning(f"Cache GET error: {e}")
            return None
    
    def set(self, object_name: str, markdown: str, page_num: Optional[int] = None):
        """Guarda OCR en cache con TTL automático"""
        if not self.enabled or not self.redis_client:
            return
        
        try:
            key = self.get_cache_key(object_name, page_num)
            self.redis_client.setex(key, self.ttl, markdown)
            logger.debug(f"💾 [CACHE SAVED] {object_name}")
        except Exception as e:
            logger.warning(f"Cache SET error: {e}")


class GPUResourceMonitor:
    """Monitorea recursos de GPU y CPU disponibles"""
    
    def __init__(self):
        self.has_cuda = False
        self.gpu_device_name = "CPU"
        self.gpu_total_memory_mb = 0
        self._detect_gpu()
    
    def _detect_gpu(self):
        """Detecta GPU NVIDIA disponible y características"""
        if getattr(settings, "force_cpu", False):
            self.has_cuda = False
            return

        try:
            import torch
            if torch.cuda.is_available():
                self.has_cuda = True
                self.gpu_device_name = torch.cuda.get_device_name(0)
                props = torch.cuda.get_device_properties(0)
                self.gpu_total_memory_mb = props.total_memory / 1024 / 1024
                logger.info(f"✅ GPU NVIDIA detectada: {self.gpu_device_name}")
            else:
                logger.info("📊 GPU no disponible, usando CPU")
        except:
            pass
    
    def get_available_gpu_memory_mb(self) -> float:
        if not self.has_cuda: return 0
        try:
            import torch
            return (torch.cuda.get_device_properties(0).total_memory - torch.cuda.memory_allocated(0)) / 1024 / 1024
        except: return 0
    
    def get_processing_device(self) -> ProcessingDevice:
        if settings.llm_provider == "runpod":
            return ProcessingDevice.HYBRID
        if self.has_cuda:
            return ProcessingDevice.GPU_OPTIMIZED
        return ProcessingDevice.CPU_PARALLEL


class DoclingVisionOptimized:
    """Motor de visión optimizado con soporte para RunPod Pods y Local GPU/CPU"""
    
    def __init__(self):
        self.cache = VisionCache()
        self.minio_client = get_minio_client()
        self.gpu_monitor = GPUResourceMonitor()
        self.processing_device = self.gpu_monitor.get_processing_device()
        self.optimal_workers = min(os.cpu_count() or 4, settings.vision_parallel_workers) if self.processing_device == ProcessingDevice.CPU_PARALLEL else 1
        logger.info(f"🚀 Vision Engine inicializado: {self.processing_device.value}, workers={self.optimal_workers}")
    
    def _get_pipeline_config(self, do_ocr: bool = True, use_gpu: bool = False) -> Tuple:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, AcceleratorOptions, AcceleratorDevice
        
        device = AcceleratorDevice.CUDA if (use_gpu and self.gpu_monitor.has_cuda) else AcceleratorDevice.CPU
        pipeline_options = PdfPipelineOptions(
            accelerator_options=AcceleratorOptions(device=device),
            do_ocr=do_ocr,
            do_table_structure=True,
            max_processing_threads=2 if use_gpu else 1
        )
        doc_converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        return (doc_converter, InputFormat)

    def _is_scanned_pdf(self, pdf_path: str) -> bool:
        try:
            import pypdf
            with open(pdf_path, 'rb') as f:
                pdf = pypdf.PdfReader(f)
                text = ""
                for page in pdf.pages[:min(3, len(pdf.pages))]:
                    text += page.extract_text() or ""
                return len(text.strip()) < settings.vision_detect_scanned_threshold
        except: return True

    async def _extract_with_runpod(self, pdf_path: str, max_retries: int = 3) -> Optional[str]:
        if not settings.runpod_docling_url or not settings.runpod_api_key: return None
        try:
            logger.info(f"🚀 Usando RunPod GPU Pod (Docling OCR): {settings.runpod_docling_url}")
            with open(pdf_path, 'rb') as f:
                pdf_bytes = f.read()
            import base64
            pdf_b64 = base64.b64encode(pdf_bytes).decode('utf-8')
            payload = {"pdf_base64": pdf_b64, "ocr_quality": settings.vision_ocr_quality, "do_table_structure": True}
            
            # Timeout configurado: 30s para conectar (Latencia Variable), 600s para lectura (Documentos largos)
            runpod_timeout = httpx.Timeout(600.0, connect=30.0)
            
            for attempt in range(max_retries):
                try:
                    logger.info(f"🔄 Intentando conexión a Docling Pod (Intento {attempt + 1}/{max_retries})...")
                    async with httpx.AsyncClient(timeout=runpod_timeout) as client:
                        response = await client.post(
                            settings.runpod_docling_url, json=payload,
                            headers={"Authorization": f"Bearer {settings.runpod_api_key}"}
                        )
                        if response.status_code in [200, 201]:
                            res = response.json()
                            return res.get("markdown") or res.get("output", {}).get("markdown", "")
                        
                        logger.warning(f"⚠️ RunPod Docling falló (Status: {response.status_code}). Reintentando...")
                except httpx.ConnectTimeout:
                    logger.warning(f"⏳ Latencia crítica detectada (>30s) en Docling Pod. Reintentando ({attempt + 1})...")
                except Exception as e_att:
                    logger.warning(f"❌ Error en intento {attempt + 1} de Docling: {e_att}")
                
                await asyncio.sleep(2)
            return None
        except Exception as e:
            logger.error(f"RunPod Docling error fatal: {e}")
            return None

    async def extract_visual_analysis_with_qwen2_vl(self, pdf_path: str, max_retries: int = 3) -> str:
        """
        [PASO 2: VISIÓN] Captura zonas críticas y analiza con Qwen2-VL en RunPod.
        Implementa reintentos para manejar latencia variable en Pods.
        """
        if not settings.runpod_vision_url or not settings.runpod_api_key:
            return ""

        try:
            import fitz  # PyMuPDF
            import base64
            
            doc = fitz.open(pdf_path)
            num_pages = len(doc)
            pages_to_extract = [0]
            if num_pages > 1: pages_to_extract.append(num_pages - 1)
            
            images_b64 = []
            for p_num in pages_to_extract:
                page = doc.load_page(p_num)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                images_b64.append(base64.b64encode(pix.tobytes("png")).decode('utf-8'))
            doc.close()

            payload = {
                "model": settings.model_vision_name,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analiza minuciosamente estas imágenes del documento legal. Detecta sellos oficiales, firmas autógrafas, hologramas y tablas. Reporta discrepancias."},
                    ] + [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}} for img in images_b64]
                }],
                "max_tokens": 1024,
                "temperature": 0.1
            }

            runpod_timeout = httpx.Timeout(600.0, connect=30.0)
            
            for attempt in range(max_retries):
                try:
                    logger.info(f"🔄 Intentando conexión a vLLM Pod Vision (Intento {attempt + 1}/{max_retries})...")
                    async with httpx.AsyncClient(timeout=runpod_timeout) as client:
                        response = await client.post(
                            settings.runpod_vision_url + "/chat/completions",
                            json=payload,
                            headers={"Authorization": f"Bearer {settings.runpod_api_key}"}
                        )
                        if response.status_code == 200:
                            analysis = response.json()["choices"][0]["message"]["content"]
                            logger.info("✅ Análisis Visual RunPod (Qwen2-VL) completado.")
                            return f"\n\n--- ANÁLISIS VISUAL DE ZONAS CRÍTICAS (Qwen2-VL) ---\n{analysis}\n------------------------------------------\n"
                        
                        logger.warning(f"⚠️ RunPod vLLM falló (Status: {response.status_code}). Reintentando...")
                except httpx.ConnectTimeout:
                    logger.warning(f"⏳ Latencia crítica detectada (>30s) en vLLM Pod. Reintentando...")
                except Exception as e_att:
                    logger.warning(f"❌ Error en intento {attempt + 1} de Vision: {e_att}")
                
                await asyncio.sleep(2)
            return ""
        except Exception as e:
            logger.warning(f"Error fatal en paso de VISIÓN Qwen2-VL: {e}")
            return ""

    async def _extract_parallel_local(self, pdf_path: str) -> str:
        if self.processing_device == ProcessingDevice.GPU_OPTIMIZED:
            doc_converter, _ = self._get_pipeline_config(do_ocr=True, use_gpu=True)
            result = doc_converter.convert(pdf_path)
            return result.document.export_to_markdown()
        
        # CPU Paralelo (simplificado para robustez)
        doc_converter, _ = self._get_pipeline_config(do_ocr=True, use_gpu=False)
        result = doc_converter.convert(pdf_path)
        return result.document.export_to_markdown()

    async def _extract_text_optimized(self, pdf_path: str) -> str:
        doc_converter, _ = self._get_pipeline_config(do_ocr=False, use_gpu=self.gpu_monitor.has_cuda)
        result = doc_converter.convert(pdf_path)
        return result.document.export_to_markdown()

    async def extract_markdown_from_minio(self, object_name: str) -> tuple[str, int, str]:
        start_time = datetime.now()
        
        # 1. Cache
        cached_data = self.cache.get(object_name)
        if cached_data:
            gpu_model = "CACHE"
            page_count = 0
            markdown = cached_data
            if "|" in cached_data:
                parts = cached_data.split("|", 2)
                markdown = parts[0]
                try: page_count = int(parts[1])
                except: pass
                if len(parts) > 2: gpu_model = parts[2]
            
            ProcessingMetrics(object_name, "CACHE", page_count, (datetime.now()-start_time).total_seconds(), len(markdown), True, False).log()
            return markdown, page_count, gpu_model

        # 2. Download
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                self.minio_client.fget_object(settings.minio_bucket, object_name, tmp.name)
                tmp_path = tmp.name
        except: return "", 0, "ERROR"

        try:
            is_scanned = self._is_scanned_pdf(tmp_path)
            markdown = None
            runpod_used = False
            
            if self.processing_device == ProcessingDevice.HYBRID:
                markdown = await self._extract_with_runpod(tmp_path)
                runpod_used = markdown is not None
            
            if not markdown:
                markdown = await self._extract_parallel_local(tmp_path) if is_scanned else await self._extract_text_optimized(tmp_path)

            # Page count
            try:
                import pypdf
                with open(tmp_path, 'rb') as f:
                    page_count = len(pypdf.PdfReader(f).pages)
            except: page_count = 1

            device_used = "RUNPOD" if runpod_used else (self.gpu_monitor.gpu_device_name if self.gpu_monitor.has_cuda else "CPU")
            
            if markdown:
                self.cache.set(object_name, f"{markdown}|{page_count}|{device_used}")
            
            elapsed = (datetime.now() - start_time).total_seconds()
            ProcessingMetrics(object_name, device_used, page_count, elapsed, len(markdown or ""), False, runpod_used).log()
            return markdown or "", page_count, device_used
        except Exception as e:
            logger.error(f"Error en visión: {e}")
            return "", 0, "ERROR"
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except: pass

    async def extract_visual_analysis(self, pdf_path: str) -> str:
        return await self.extract_visual_analysis_with_qwen2_vl(pdf_path)

vision_engine = DoclingVisionOptimized()

async def extract_markdown_from_minio_async(object_name: str) -> tuple[str, int, str]:
    return await vision_engine.extract_markdown_from_minio(object_name)

async def extract_visual_analysis_async(pdf_path: str) -> str:
    return await vision_engine.extract_visual_analysis(pdf_path)

def extract_markdown_from_minio_sync(object_name: str) -> tuple[str, int, str]:
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                return executor.submit(asyncio.run, extract_markdown_from_minio_async(object_name)).result()
        return loop.run_until_complete(extract_markdown_from_minio_async(object_name))
    except:
        return asyncio.run(extract_markdown_from_minio_async(object_name))

def extract_visual_analysis_sync(pdf_path: str) -> str:
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                return executor.submit(asyncio.run, extract_visual_analysis_async(pdf_path)).result()
        return loop.run_until_complete(extract_visual_analysis_async(pdf_path))
    except:
        return asyncio.run(extract_visual_analysis_async(pdf_path))
