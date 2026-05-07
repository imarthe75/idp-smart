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

# Defer docling imports to avoid crashes on slim workers
# from docling.datamodel.base_models import InputFormat
# from docling.datamodel.pipeline_options import PdfPipelineOptions
# from docling.document_converter import DocumentConverter, PdfFormatOption
# from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend

from core.config import settings

import threading
import fcntl

logger = logging.getLogger(__name__)
_INIT_LOCK_FILE = "/app/.ocr_init.lock"
_global_converter = None
_global_lock = threading.Lock()

class DoclingVisionOptimized:
    def __init__(self):
        """
        Inicia el motor Docling con soporte para saneamiento de memoria y ARQUITECTURA AUTOADAPTABLE.
        """
        self.storage_client = None
        from engine.hardware_detector import detect_hardware
        self.profile = detect_hardware()
        
    def _get_pipeline_config(self):
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True
        pipeline_options.accelerator_options.device = "cpu"
        num_threads = max(1, self.profile.cpu_cores // self.profile.max_parallel_batches)
        pipeline_options.accelerator_options.num_threads = num_threads
        return pipeline_options

    def _get_converter(self):
        global _global_converter
        if _global_converter is None:
            with _global_lock:
                if _global_converter is None:
                    from docling.datamodel.base_models import InputFormat
                    from docling.datamodel.pipeline_options import PdfPipelineOptions
                    from docling.document_converter import DocumentConverter, PdfFormatOption
                    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
                    
                    pipeline_options = self._get_pipeline_config()
                    lock_file = open(_INIT_LOCK_FILE, "w")
                    try:
                        fcntl.flock(lock_file, fcntl.LOCK_EX)
                        logger.info("🛠️ [OCR] Inicializando DocumentConverter...")
                        conv = DocumentConverter(
                            allowed_formats=[InputFormat.PDF],
                            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options, backend=PyPdfiumDocumentBackend)}
                        )
                        _global_converter = conv
                    finally:
                        fcntl.flock(lock_file, fcntl.LOCK_UN)
                        lock_file.close()
        return _global_converter

    def _process_chunk_with_timeout(self, file_path: str, timeout: int = 300) -> str:
        try:
            converter = self._get_converter()
            result = converter.convert(file_path)
            return result.document.export_to_markdown()
        except Exception as e:
            logger.error(f"❌ Error lote local: {e}")
            raise RuntimeError(f"Fallo en lote OCR: {e}")

    def _download_from_storage(self, object_name: str) -> str:
        """Descarga preservando la extensión real."""
        from core.storage_client import get_storage_client
        client = get_storage_client()
        ext = ".pdf"
        if object_name.lower().endswith((".tif", ".tiff")):
            ext = ".tif"
        
        tmp_p = os.path.join(tempfile.gettempdir(), f"raw_{uuid.uuid4()}{ext}")
        client.fget_object(settings.storage_bucket, object_name, tmp_p)
        return tmp_p

    def _extract_markdown_local(self, object_names: list[str]) -> tuple[str, int, str]:
        if isinstance(object_names, str):
            object_names = [object_names]
            
        tmp_files = []
        pdf_paths = []
        
        try:
            # 1. Descargar y normalizar todos los archivos a PDF
            for obj in object_names:
                local_path = self._download_from_storage(obj)
                tmp_files.append(local_path)
                
                if local_path.lower().endswith((".tif", ".tiff")):
                    import fitz
                    logger.info(f"🔄 [TIFF Converter] Transformando TIFF a PDF: {obj}")
                    with fitz.open(local_path) as tiff_doc:
                        pdf_bytes = tiff_doc.convert_to_pdf()
                        pdf_path = os.path.join(tempfile.gettempdir(), f"conv_{uuid.uuid4()}.pdf")
                        with open(pdf_path, "wb") as f_pdf:
                            f_pdf.write(pdf_bytes)
                        pdf_paths.append(pdf_path)
                        tmp_files.append(pdf_path)
                else:
                    pdf_paths.append(local_path)

            # 2. Concatenar todos los PDFs en uno solo si hay más de uno
            if len(pdf_paths) > 1:
                from pypdf import PdfWriter
                writer = PdfWriter()
                for p in pdf_paths:
                    writer.append(p)
                
                merged_path = os.path.join(tempfile.gettempdir(), f"merged_{uuid.uuid4()}.pdf")
                with open(merged_path, "wb") as f_merged:
                    writer.write(f_merged)
                pdf_path_to_process = merged_path
                tmp_files.append(merged_path)
            else:
                pdf_path_to_process = pdf_paths[0]

            from pypdf import PdfReader
            if not os.path.exists(pdf_path_to_process) or os.path.getsize(pdf_path_to_process) == 0:
                raise ValueError("Archivo no encontrado o vacío tras descarga/concatenación.")


            reader = PdfReader(pdf_path_to_process)
            total_pages = len(reader.pages)
            if total_pages == 0:
                raise ValueError("El documento no tiene páginas procesables.")

            effective_chunk = self.profile.pdf_chunk_size
            max_parallel_batches = self.profile.max_parallel_batches
            strategy = f"Serial-Power-{effective_chunk}x{max_parallel_batches}"
            logger.info(f"🚀 [ESTRATEGIA] {strategy} | Docs: {len(object_names)} | Págs: {total_pages} | RAM: {self.profile.ram_total_gb}GB")


            results_map = {}
            if max_parallel_batches <= 1:
                # Modo Serial
                for start in range(0, total_pages, effective_chunk):
                    end = min(start + effective_chunk, total_pages)
                    chunk_id = start // effective_chunk
                    chunk_p = self._create_chunk(reader, start, end, chunk_id)
                    tmp_files.append(chunk_p)
                    md_part = self._process_chunk_with_timeout(chunk_p)
                    results_map[chunk_id] = md_part
                    logger.info(f"✅ Segmento {chunk_id} ({start+1}-{end}) completado.")
            else:
                # Modo Paralelo
                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=max_parallel_batches) as executor:
                    futures = {}
                    for start in range(0, total_pages, effective_chunk):
                        end = min(start + effective_chunk, total_pages)
                        chunk_id = start // effective_chunk
                        chunk_p = self._create_chunk(reader, start, end, chunk_id)
                        tmp_files.append(chunk_p)
                        futures[executor.submit(self._process_chunk_with_timeout, chunk_p)] = chunk_id

                    for future in as_completed(futures):
                        cid = futures[future]
                        results_map[cid] = future.result()
                        logger.info(f"✅ Segmento paralelo {cid} completado.")

            full_markdown = "\n\n".join(results_map[i] for i in sorted(results_map.keys()))
            return full_markdown, total_pages, strategy

        except Exception as e:
            logger.error(f"❌ Error en Visión: {e}")
            return f"ERROR: {str(e)}", 0, "ERROR"
        finally:
            for f in tmp_files:
                if f and os.path.exists(f):
                    try: os.unlink(f)
                    except: pass
            import gc
            gc.collect()

    def _create_chunk(self, reader, start: int, end: int, chunk_id: int) -> str:
        """Crea un archivo PDF temporal con un rango de páginas."""
        chunk_p = os.path.join(tempfile.gettempdir(), f"chunk_{chunk_id}_{uuid.uuid4()}.pdf")
        from pypdf import PdfWriter
        writer = PdfWriter()
        for p_idx in range(start, end):
            writer.add_page(reader.pages[p_idx])
        with open(chunk_p, "wb") as f_out:
            writer.write(f_out)
        return chunk_p

    def extract_markdown_from_storage(self, object_names: list[str]) -> tuple[str, int, str]:
        """
        Deriva la extracción a un microservicio remoto si está configurado,
        de lo contrario intenta el procesamiento local. 
        Soporta una lista de objetos que serán concatenados.
        """
        if isinstance(object_names, str):
            object_names = [object_names]

        if settings.ocr_engine == "vision_llm":
            logger.info("🎨 [VISION_LLM] Iniciando transcripción visual con Qwen2-VL...")
            return self._extract_markdown_with_vision_llm(object_names)

        if settings.docling_server_url:
            local_path = None
            try:
                import requests
                logger.info(f"🌐 [REMOTE] Llamando a microservicio Docling (v1): {settings.docling_server_url}")
                
                if len(object_names) > 1:
                    from pypdf import PdfWriter
                    writer = PdfWriter()
                    temp_paths = []
                    for obj in object_names:
                        p = self._download_from_storage(obj)
                        if p.lower().endswith((".tif", ".tiff")):
                            import fitz
                            with fitz.open(p) as tiff_doc:
                                pb = tiff_doc.convert_to_pdf()
                                p2 = os.path.join(tempfile.gettempdir(), f"conv_rem_{uuid.uuid4()}.pdf")
                                with open(p2, "wb") as f: f.write(pb)
                                p = p2
                                temp_paths.append(p2)
                        
                        writer.append(p)
                        temp_paths.append(p)
                    
                    local_path = os.path.join(tempfile.gettempdir(), f"merged_rem_{uuid.uuid4()}.pdf")
                    with open(local_path, "wb") as f: writer.write(f)
                    for tp in temp_paths: 
                        if tp != local_path: os.unlink(tp)
                else:
                    local_path = self._download_from_storage(object_names[0])
                
                filename = "merged_document.pdf"
                with open(local_path, 'rb') as f:
                    # El microservicio v1 espera el campo 'files'
                    files = {'files': (filename, f, 'application/pdf')}
                    target_url = settings.docling_server_url
                    # Autocorrección de endpoint v1
                    if "/extract" in target_url:
                        target_url = target_url.replace("/extract", "/v1/convert/file")
                    elif not "/v1/" in target_url:
                        if not target_url.endswith("/"): target_url += "/"
                        target_url += "v1/convert/file"

                    resp = requests.post(target_url, files=files, timeout=settings.proxy_timeout_s * 2)
                    
                resp.raise_for_status()
                data = resp.json()
                
                # En v1 la respuesta está en data["document"]["md_content"]
                doc_data = data.get("document", {})
                markdown = doc_data.get("md_content")
                
                if not markdown:
                    if data.get("errors"):
                         raise RuntimeError(f"Docling Error: {data['errors']}")
                    raise RuntimeError("Docling no devolvió markdown")
                    
                return markdown, data.get("total_pages", 0), "Remote-Docling-v1"
            except Exception as e:
                logger.error(f"⚠️ [REMOTE] Error llamando al microservicio: {e}")
                if self._can_process_locally():
                     return self._extract_markdown_local(object_names)
                raise RuntimeError(f"Fallo en extracción remota y local no disponible: {e}")
            finally:
                if local_path and os.path.exists(local_path):
                    try: os.unlink(local_path)
                    except: pass
        
        return self._extract_markdown_local(object_names)


    def _can_process_locally(self) -> bool:
        try:
            import docling
            return True
        except ImportError:
            return False

    def extract_visual_analysis_sync(self, pdf_path: str) -> str:
        """
        Realiza análisis visual (Qwen2-VL) sobre las páginas clave del PDF.
        Soporta cacheo persistente si el IDP_SMART_MODE lo permite.
        """
        if not settings.runpod_enabled or not settings.runpod_vision_url:
            logger.info("ℹ️ [VISION] RunPod o URL de visión no configurados. Saltando análisis visual.")
            return ""

        try:
            import fitz
            import base64
            import requests

            logger.info(f"🎨 [VISION] Iniciando análisis visual RunPod: {pdf_path}")
            doc = fitz.open(pdf_path)
            # Analizar portada, mitad y final (o solo portada si es corto)
            indices = [0]
            if len(doc) > 1: indices.append(len(doc) // 2)
            if len(doc) > 2: indices.append(len(doc) - 1)
            
            # Limitar a máx 3 imágenes para no saturar el prompt
            indices = sorted(list(set(indices)))[:3]
            
            images_b64 = []
            for idx in indices:
                page = doc.load_page(idx)
                # Renderizar a 2x para buena legibilidad (144 DPI)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_data = pix.tobytes("png")
                images_b64.append(base64.b64encode(img_data).decode("utf-8"))
            doc.close()

            prompt = """
            Eres un experto en documentos notariales mexicanos. Analiza estas imágenes y describe la evidencia visual crítica:
            1. SELLOS: Menciona si hay sellos notariales, del Registro Público o de otras autoridades.
            2. FIRMAS: Identifica si hay firmas autógrafas y en qué parte están.
            3. LOGOS: Identifica logotipos de notarias o dependencias gubernamentales.
            4. INTEGRIDAD: Reporta tachaduras, enmendaduras o sellos de 'CANCELADO'.
            Responde de forma técnica y concisa.
            """

            # Preparar payload para Qwen2-VL (compatible con OpenAI Vision API si vLLM está configurado)
            content = [{"type": "text", "text": prompt}]
            for b64 in images_b64:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"}
                })

            payload = {
                "model": "qwen2-vl", # Ajustable según el deployment del pod
                "messages": [{"role": "user", "content": content}],
                "max_tokens": 1024,
                "temperature": 0.1
            }

            headers = {
                "Authorization": f"Bearer {settings.runpod_api_key}",
                "Content-Type": "application/json"
            }

            resp = requests.post(
                settings.runpod_vision_url,
                headers=headers,
                json=payload,
                timeout=settings.proxy_timeout_s
            )
            resp.raise_for_status()
            
            result = resp.json()
            analysis_text = result["choices"][0]["message"]["content"]
            
            logger.info("✅ [VISION] Análisis visual completado exitosamente.")
            return f"\n--- ANÁLISIS VISUAL DE EVIDENCIA (NOTARIAL) ---\n{analysis_text}\n"

        except Exception as e:
            logger.error(f"⚠️ [VISION] Error en análisis visual: {e}")
            return ""

    def _extract_markdown_with_vision_llm(self, object_names: list[str]) -> tuple[str, int, str]:
        """
        Usa un modelo de visión (Qwen2-VL) para transcribir las imágenes a Markdown.
        """
        try:
            import fitz
            import base64
            
            all_md = []
            total_pages = 0
            
            for obj in object_names:
                local_path = self._download_from_storage(obj)
                doc = fitz.open(local_path)
                total_pages += len(doc)
                
                for i in range(len(doc)):
                    page = doc.load_page(i)
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                    img_data = pix.tobytes("png")
                    b64 = base64.b64encode(img_data).decode("utf-8")
                    
                    prompt = "Transcribe el contenido de esta página de un documento notarial a formato Markdown fielmente. No inventes nada. Solo el texto y tablas si las hay."
                    
                    payload = {
                        "model": settings.model_vision_name,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
                            ]
                        }],
                        "max_tokens": 2048,
                        "temperature": 0.1
                    }
                    
                    headers = {
                        "Authorization": f"Bearer {settings.nvidia_nim_api_key}",
                        "Content-Type": "application/json"
                    }
                    
                    resp = requests.post(
                        f"{settings.nvidia_nim_base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=settings.proxy_timeout_s
                    )
                    resp.raise_for_status()
                    res = resp.json()
                    all_md.append(res["choices"][0]["message"]["content"])
                
                doc.close()
                os.unlink(local_path)
            
            return "\n\n".join(all_md), total_pages, f"VisionLLM-{settings.model_vision_name}"
        except Exception as e:
            logger.error(f"❌ Error en VisionLLM: {e}")
            return self._extract_markdown_local(object_names)

vision_engine = DoclingVisionOptimized()

def extract_markdown_from_storage(object_name: str) -> tuple[str, int, str]:
    return vision_engine.extract_markdown_from_storage(object_name)

def extract_visual_analysis_sync(pdf_path: str) -> str:
    return vision_engine.extract_visual_analysis_sync(pdf_path)
