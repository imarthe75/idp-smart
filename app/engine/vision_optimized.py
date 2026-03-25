"""
🚀 Vision Engine Optimizado para GPU NVIDIA + RunPod
- Detección inteligente de scaneados vs texto
- Procesamiento adaptativo: paralelo en CPU, serial optimizado en GPU
- Batching dinámico basado en memoria disponible
- Cache Redis/Valkey para OCR
- Monitoreo de performance y métricas GPU
- Preparado para GPU NVIDIA + RunPod Serverless con fallback inteligente
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
        return f"docling:ocr:v3:{hashlib.md5(key_part.encode()).hexdigest()}"
    
    def get(self, object_name: str, page_num: Optional[int] = None) -> Optional[str]:
        """Obtiene OCR del cache (O(1) lookup)"""
        if not self.enabled or not self.redis_client:
            return None
        
        try:
            key = self.get_cache_key(object_name, page_num)
            value = self.redis_client.get(key)
            if value:
                logger.info(f"💾 [CACHE HIT] {object_name}:{page_num or 'full'}")
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
            logger.debug(f"💾 [CACHE SAVED] {object_name}:{page_num or 'full'}")
        except Exception as e:
            logger.warning(f"Cache SET error: {e}")
    
    def invalidate(self, object_name: str):
        """Invalida cache de un documento"""
        if not self.enabled or not self.redis_client:
            return
        
        try:
            pattern = f"docling:ocr:v3:{hashlib.md5(f'{object_name}:*'.encode()).hexdigest()}*"
            keys = self.redis_client.keys(pattern)
            if keys:
                self.redis_client.delete(*keys)
                logger.info(f"🗑️ Cache invalidado: {object_name}")
        except Exception as e:
            logger.warning(f"Cache invalidate error: {e}")


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
            logger.info("📊 FORCE_CPU is True. Forzando procesamiento en CPU.")
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
                logger.info(f"📊 Memoria GPU: {self.gpu_total_memory_mb:.0f} MB")
            else:
                logger.info("📊 GPU no disponible, usando CPU")
        except ImportError:
            logger.debug("PyTorch no disponible para GPU check")
        except Exception as e:
            logger.warning(f"Error detectando GPU: {e}")
    
    def get_available_cpu_memory_mb(self) -> float:
        """Obtiene memoria CPU disponible"""
        try:
            return psutil.virtual_memory().available / 1024 / 1024
        except:
            return 2048.0  # Default
    
    def get_available_gpu_memory_mb(self) -> float:
        """Obtiene memoria GPU disponible"""
        if not self.has_cuda:
            return 0
        
        try:
            import torch
            return (torch.cuda.get_device_properties(0).total_memory - torch.cuda.memory_allocated(0)) / 1024 / 1024
        except:
            return self.gpu_total_memory_mb * 0.8  # Estimado
    
    def get_processing_device(self) -> ProcessingDevice:
        """Retorna estrategia de procesamiento recomendada"""
        # RunPod tiene prioridad si está habilitado
        if settings.docling_runpod_enabled and settings.docling_runpod_endpoint:
            return ProcessingDevice.HYBRID  # Intentar RunPod con fallback local
        
        # Si hay GPU NVIDIA, usarla
        if self.has_cuda:
            return ProcessingDevice.GPU_OPTIMIZED
        
        # Default: CPU paralelo
        return ProcessingDevice.CPU_PARALLEL
    
    def get_optimal_parallel_workers(self, device: ProcessingDevice) -> int:
        """Calcula número óptimo de workers según device y memoria"""
        if device == ProcessingDevice.GPU_OPTIMIZED:
            # En GPU: 1 worker (procesamiento serial optimizado)
            return 1
        
        if device == ProcessingDevice.CPU_PARALLEL:
            # En CPU: número de cores disponibles, máx 6
            cpu_count = os.cpu_count() or 4
            return min(cpu_count, settings.vision_parallel_workers)
        
        return 1
    
    def recommend_batch_size(self, device: ProcessingDevice) -> int:
        """Recomienda tamaño de batch según device"""
        if device == ProcessingDevice.GPU_OPTIMIZED:
            # GPU: procesar más páginas sin sobrecarga
            gpu_gb = self.gpu_total_memory_mb / 1024
            if gpu_gb >= 8:
                return 4  # Procesar 4 páginas en paralelo en GPU
            elif gpu_gb >= 4:
                return 2
            else:
                return 1
        
        if device == ProcessingDevice.CPU_PARALLEL:
            # CPU: batch pequeño de 1 página por worker
            return 1
        
        return 1


class DoclingVisionOptimized:
    """Motor de visión optimizado con estrategias adaptativas para CPU/GPU/RunPod"""
    
    def __init__(self):
        self.cache = VisionCache()
        self.minio_client = get_minio_client()
        self.gpu_monitor = GPUResourceMonitor()
        
        # Determinar estrategia
        self.processing_device = self.gpu_monitor.get_processing_device()
        self.optimal_workers = self.gpu_monitor.get_optimal_parallel_workers(self.processing_device)
        
        # Executor para paralelismo (solo si CPU paralelo)
        self.executor = None
        if self.processing_device == ProcessingDevice.CPU_PARALLEL:
            self.executor = ThreadPoolExecutor(
                max_workers=self.optimal_workers,
                thread_name_prefix="docling_cpu_"
            )
        
        logger.info(f"🚀 Vision Engine inicializado: {self.processing_device.value}, workers={self.optimal_workers}")
    
    def _get_pipeline_config(self, do_ocr: bool = True, use_gpu: bool = False) -> Tuple:
        """Retorna configuración optimizada de Docling pipeline"""
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            AcceleratorOptions,
            AcceleratorDevice,
        )
        
        # Seleccionar device
        if use_gpu and self.gpu_monitor.has_cuda:
            device = AcceleratorDevice.CUDA
        else:
            device = AcceleratorDevice.CPU
        
        # Configurar según tipo de procesamiento
        max_threads = 2 if use_gpu else 1  # GPU con threading es contraproducente
        
        accelerator_options = AcceleratorOptions(device=device)
        pipeline_options = PdfPipelineOptions(
            accelerator_options=accelerator_options,
            do_ocr=do_ocr,
            do_table_structure=True,
            max_processing_threads=max_threads
        )
        
        doc_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        
        return (doc_converter, InputFormat)
    
    def _is_scanned_pdf(self, pdf_path: str) -> bool:
        """
        Detecta si PDF es scaneado (imagen) o tiene texto extractable.
        Retorna True si < threshold caracteres = necesita OCR completo
        """
        try:
            import pypdf
            
            with open(pdf_path, 'rb') as f:
                pdf = pypdf.PdfReader(f)
                
                # Extraer texto de primeras 3 páginas
                extracted_text = ""
                for page in pdf.pages[:min(3, len(pdf.pages))]:
                    extracted_text += page.extract_text() or ""
                
                is_scanned = len(extracted_text.strip()) < settings.vision_detect_scanned_threshold
                
                char_count = len(extracted_text.strip())
                pdf_type = "SCANEADO (OCR needed)" if is_scanned else "TEXTO (fast path)"
                
                logger.info(
                    f"📄 PDF Analysis: "
                    f"Pages={len(pdf.pages)}, "
                    f"Extracted={char_count} chars, "
                    f"Type={pdf_type}"
                )
                return is_scanned
        
        except Exception as e:
            logger.warning(f"Error detectando tipo PDF: {e}, asumiendo scaneado")
            return True
    
    async def _extract_with_runpod(self, pdf_path: str, max_retries: int = 3) -> Optional[str]:
        """
        Extrae usando RunPod Serverless con retry logic y fallback.
        Estrategia para procesamiento distribuido a escala.
        """
        if not settings.docling_runpod_enabled or not settings.docling_runpod_endpoint:
            return None
        
        try:
            logger.info("🚀 Usando RunPod Serverless para OCR distribuido")
            
            # Leer archivo
            with open(pdf_path, 'rb') as f:
                pdf_bytes = f.read()
            
            # Base64 encode
            import base64
            pdf_b64 = base64.b64encode(pdf_bytes).decode('utf-8')
            
            # Payload para RunPod
            payload = {
                "input": {
                    "pdf_base64": pdf_b64,
                    "ocr_quality": settings.vision_ocr_quality,
                    "do_table_structure": True
                }
            }
            
            # Invocar con retries
            for attempt in range(max_retries):
                try:
                    async with httpx.AsyncClient(timeout=settings.docling_runpod_timeout) as client:
                        response = await client.post(
                            f"{settings.docling_runpod_endpoint.rstrip('/')}/run",
                            json=payload,
                            headers={"Authorization": f"Bearer {settings.docling_runpod_api_key}"},
                            follow_redirects=True
                        )
                        
                        if response.status_code in [200, 201]:
                            result = response.json()
                            markdown = result.get("output", {}).get("markdown", "")
                            
                            if markdown:
                                logger.info(f"✅ RunPod OCR exitoso (Intent {attempt + 1})")
                                return markdown
                        
                        elif response.status_code == 429:  # Rate limit
                            logger.warning(f"⚠️ RunPod rate limit, reintentando... ({attempt + 1}/{max_retries})")
                            await asyncio.sleep(2 ** attempt)  # Exponential backoff
                            continue
                        
                        else:
                            logger.error(f"RunPod error: {response.status_code} - {response.text[:200]}")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(1)
                            continue
                
                except httpx.TimeoutException:
                    logger.warning(f"RunPod timeout, reintentando... ({attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1)
                    continue
            
            logger.warning("❌ RunPod falló después de retries, usando local fallback")
            return None
        except Exception as e:
            logger.error(f"RunPod error: {e}")
            return None
    
    def _extract_page_local(self, pdf_path: str, page_num: int, use_gpu: bool) -> tuple:
        """Extrae una sola página usando Local GPU/CPU."""
        import tempfile
        import os
        from pypdf import PdfReader, PdfWriter
        
        tmp_pdf = None
        try:
            # Create a single-page PDF to bypass Docling API changes in page_nums
            reader = PdfReader(pdf_path)
            writer = PdfWriter()
            writer.add_page(reader.pages[page_num])
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                writer.write(tmp)
                tmp_pdf = tmp.name
            
            # Recrea el doc_converter por hilo para evitar issues de concurrencia de PyTorch
            doc_converter, _ = self._get_pipeline_config(do_ocr=True, use_gpu=use_gpu)
            
            result = doc_converter.convert(tmp_pdf)
            markdown = result.document.export_to_markdown()
            return page_num, markdown
            
        except Exception as e:
            logger.error(f"Error extrayendo página {page_num}: {e}")
            return page_num, ""
        finally:
            if tmp_pdf and os.path.exists(tmp_pdf):
                os.remove(tmp_pdf)
    
    async def _extract_parallel_local(self, pdf_path: str) -> str:
        """
        Procesa PDF scaneado adaptándose al device disponible.
        - CPU: Paralelo con múltiples threads (4 por defecto)
        - GPU: Serial optimizado para máximo throughput de GPU
        - Ambos: Batching inteligente basado en recursos
        """
        try:
            import pypdf
            
            with open(pdf_path, 'rb') as f:
                pdf = pypdf.PdfReader(f)
                num_pages = len(pdf.pages)
            
            logger.info(
                f"📄 Procesando {num_pages} páginas "
                f"({'GPU paralelo' if self.gpu_monitor.has_cuda else 'CPU paralelo'})"
            )
            
            # GPU: Procesamiento serial (GPU ya es paralelo internamente)
            if self.processing_device == ProcessingDevice.GPU_OPTIMIZED:
                doc_converter, _ = self._get_pipeline_config(do_ocr=True, use_gpu=True)
                result = doc_converter.convert(pdf_path)
                markdown = result.document.export_to_markdown()
                logger.info(f"✅ GPU OCR completado: {len(markdown)} chars")
                return markdown
            
            # CPU: Procesamiento paralelo con threads
            results = {}
            futures = {}
            
            with ThreadPoolExecutor(max_workers=self.optimal_workers, thread_name_prefix="docling_ocr_") as executor:
                for page_num in range(num_pages):
                    future = executor.submit(
                        self._extract_page_local,
                        pdf_path,
                        page_num,
                        use_gpu=False
                    )
                    futures[future] = page_num
                
                for future in as_completed(futures):
                    page_num, markdown = future.result()
                    results[page_num] = markdown
                    logger.debug(f"✅ Página {page_num} completada")
            
            # Combinar en orden
            full_markdown = "\n".join(
                results[i] for i in sorted(results.keys()) if results[i]
            )
            
            logger.info(f"✅ CPU paralelo completado: {len(full_markdown)} chars, {num_pages} páginas")
            return full_markdown
        
        except Exception as e:
            logger.error(f"Error en procesamiento paralelo: {e}")
            return ""
    
    async def _extract_text_optimized(self, pdf_path: str) -> str:
        """
        Extrae PDFs de texto puro (sin OCR).
        Optimizado para ambos CPU y GPU.
        """
        try:
            logger.info("⚡ Extrayendo PDF de texto (sin OCR necesario)")
            
            use_gpu = self.gpu_monitor.has_cuda
            doc_converter, _ = self._get_pipeline_config(do_ocr=False, use_gpu=use_gpu)
            
            result = doc_converter.convert(pdf_path)
            markdown = result.document.export_to_markdown()
            
            device_name = "GPU" if use_gpu else "CPU"
            logger.info(f"✅ Extracción {device_name}: {len(markdown)} chars")
            return markdown
        
        except Exception as e:
            logger.error(f"Error extrayendo PDF: {e}")
            return ""

    async def _extract_visual_structure_with_qwen2_vl(self, pdf_path: str) -> str:
        """
        [Fase de Visión - Qwen2-VL]
        Analiza la estructura visual del PDF (identificación de sellos, firmas, tablas complejas).
        """
        try:
            import base64
            # Para Qwen2-VL asumiendo que el modelo detrás de LocalAI soporta PDFs en base64
            # o imágenes extraídas. Aquí se codificará todo el PDF temporalmente.
            with open(pdf_path, "rb") as pdf_file:
                pdf_b64 = base64.b64encode(pdf_file.read()).decode("utf-8")
                
            from langchain_openai import ChatOpenAI
            llm_vision = ChatOpenAI(
                base_url=settings.localai_url,
                api_key="not-needed",
                model=settings.model_vision,
                temperature=0.1,
                max_tokens=1024
            )
            
            message = {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Fase de Visión: Analiza meticulosamente este documento (estructura visual, sellos, firmas, y tablas complejas). Retorna solo los hallazgos críticos de la estructura."
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:application/pdf;base64,{pdf_b64}"
                        }
                    }
                ]
            }
            logger.info(f"👁️ Iniciando análisis Qwen2-VL en {settings.localai_url}")
            response = llm_vision.invoke([message])
            analysis = response.content
            logger.info(f"✅ Análisis Qwen2-VL completado: {len(analysis)} chars.")
            return f"\n\n--- ANÁLISIS VISUAL (QWEN2-VL) ---\n{analysis}\n-----------------------------------\n"
        except Exception as e:
            logger.warning(f"Error en extraccion visual con Qwen2-VL: {e}")
            return "\n\n--- ANÁLISIS VISUAL (QWEN2-VL) FALLIDO O NO DISPONIBLE ---\n"
    
    async def extract_markdown_from_minio(self, object_name: str) -> tuple[str, int]:
        """
        Extrae markdown de PDF en MinIO con estrategia adaptativa:
        1. Cache check (O(1) - instant si hit)
        2. Detecta tipo de PDF (scaneado vs texto)
        3. RunPod (si habilitado, con fallback)
        4. Local (GPU o CPU paralelo según disponibilidad)
        5. Guarda en cache
        
        Métricas captureadas automáticamente.
        """
        
        start_time = datetime.now()
        logger.info(f"🔍 Iniciando extracción: {object_name}")
        
        # 1. Verificar cache (INSTANT si hit - 0.1s)
        # El cache guarda "markdown|page_count"
        cached_data = self.cache.get(object_name)
        if cached_data:
            markdown = cached_data
            page_count = 0
            if "|" in cached_data:
                parts = cached_data.split("|", 1)
                markdown = parts[0]
                try: page_count = int(parts[1])
                except: page_count = 0

            metrics = ProcessingMetrics(
                object_name=object_name,
                device_used="CACHE",
                pages_processed=page_count,
                time_elapsed=(datetime.now() - start_time).total_seconds(),
                characters_output=len(markdown),
                cache_hit=True,
                runpod_used=False
            )
            metrics.log()
            return markdown, page_count
        
        # 2. Descargar PDF
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                self.minio_client.fget_object(
                    settings.minio_bucket,
                    object_name,
                    tmp_file.name
                )
                tmp_path = tmp_file.name
        except Exception as e:
            logger.error(f"Error descargando de MinIO: {e}")
            return "", 0
        
        try:
            # 3. Detectar tipo de PDF
            is_scanned = self._is_scanned_pdf(tmp_path)
            
            markdown = None
            page_count = 0
            runpod_used = False
            
            # 4. Intentar RunPod si está habilitado (estrategia HYBRID)
            if self.processing_device == ProcessingDevice.HYBRID:
                logger.info("🔄 Intentando RunPod (HYBRID mode)...")
                runpod_result = await self._extract_with_runpod(tmp_path)
                if runpod_result:
                    markdown = runpod_result
                    runpod_used = True
                    logger.info("✅ RunPod exitoso")
                else:
                    logger.info("⚠️ RunPod falló, fallback a local")
            
            # 5. Procesar localmente si no hay RunPod o es fallback
            if not markdown:
                if is_scanned:
                    # _extract_parallel_local returns markdown string, we need to get pages from it or the file
                    markdown = await self._extract_parallel_local(tmp_path)
                    try:
                        import pypdf
                        with open(tmp_path, 'rb') as f:
                            page_count = len(pypdf.PdfReader(f).pages)
                    except: page_count = 1
                else:
                    markdown = await self._extract_text_optimized(tmp_path)
                    try:
                        import pypdf
                        with open(tmp_path, 'rb') as f:
                            page_count = len(pypdf.PdfReader(f).pages)
                    except: page_count = 1

            # Fase de Visión Qwen2-VL (Comentado temporalmente por fallos en LocalAI)
            # qwen_analysis = await self._extract_visual_structure_with_qwen2_vl(tmp_path)
            # markdown = (markdown or "") + qwen_analysis
            
            # 6. Guardar en cache
            if markdown:
                # Cacheamos con separador para recuperar page_count
                self.cache.set(object_name, f"{markdown}|{page_count}")
            
            # Log métricas
            elapsed = (datetime.now() - start_time).total_seconds()
            device_name = self.processing_device.value if not runpod_used else "RUNPOD"
            
            metrics = ProcessingMetrics(
                object_name=object_name,
                device_used=device_name,
                pages_processed=page_count,
                time_elapsed=elapsed,
                characters_output=len(markdown or ""),
                cache_hit=False,
                runpod_used=runpod_used,
                gpu_memory_used_mb=self.gpu_monitor.get_available_gpu_memory_mb() if self.gpu_monitor.has_cuda else 0,
                cpu_memory_used_mb=0  # ignorar psutil error en linter si no queremos import fallido, o dejar local
            )
            metrics.log()
            
            logger.info(f"✅ Extracción completada: {len(markdown or '')} caracteres en {elapsed:.2f}s")
            return markdown or "", page_count
        
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)


# Instancia global optimizada
vision_engine = DoclingVisionOptimized()


async def extract_markdown_from_minio(object_name: str) -> tuple[str, int]:
    """
    Punto de entrada público para extracción de OCR.
    Usa todas las optimizaciones automáticamente.
    Retorna (markdown_text, page_count)
    """
    return await vision_engine.extract_markdown_from_minio(object_name)


# ============================================
# BACKWARD COMPATIBILITY
# ============================================

def extract_markdown_from_minio_sync(object_name: str) -> tuple[str, int]:
    """
    Versión síncrona para compatibilidad con Celery.
    Retorna (markdown_text, page_count)
    """
    import asyncio
    import sys
    
    try:
        # Intentar obtener event loop existente
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Si hay un loop ejecutándose (ej: en Celery), crear uno nuevo
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    extract_markdown_from_minio(object_name)
                )
                return future.result()
        else:
            # Si el loop existe pero no está corriendo, usarlo
            return loop.run_until_complete(extract_markdown_from_minio(object_name))
    except RuntimeError:
        # No hay event loop, crear uno nuevo
        return asyncio.run(extract_markdown_from_minio(object_name))
