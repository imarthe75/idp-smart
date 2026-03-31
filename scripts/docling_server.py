import base64
import os
import tempfile
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
import uvicorn

# Configuración de Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docling-server")

app = FastAPI(title="Docling GPU Server for RunPod")

# Inicialización global del convertidor (Singleton)
pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = True
pipeline_options.do_table_structure = True
# Si hay GPU, docling la detectará automáticamente. 
# En RunPod L40S/RTX4090 esto debería ser transparente.

converter = DocumentConverter(
    allowed_formats=[InputFormat.PDF],
    format_options={
        InputFormat.PDF: PdfFormatOption(
            pipeline_options=pipeline_options, 
            backend=PyPdfiumDocumentBackend
        )
    }
)

class DoclingOptions(BaseModel):
    do_ocr: bool = True
    do_table_structure: bool = True

class DoclingInput(BaseModel):
    pdf_base64: str
    options: DoclingOptions = DoclingOptions()

class RunPodPayload(BaseModel):
    input: DoclingInput

@app.get("/")
async def health():
    return {"status": "ready", "engine": "docling"}

@app.post("/")
async def process_document(payload: RunPodPayload):
    """
    Endpoint compatible con el formato RunPod Serverless / Pod Proxy.
    Recibe un PDF en base64 y devuelve el Markdown extraído.
    """
    try:
        pdf_data = base64.b64decode(payload.input.pdf_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64: {str(e)}")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_data)
        tmp_path = tmp.name

    try:
        logger.info(f"Procesando documento...")
        result = converter.convert(tmp_path)
        markdown = result.document.export_to_markdown()
        logger.info(f"Procesamiento completado. Chars: {len(markdown)}")
        
        # Devolvemos en el formato que espera el orquestador
        return {
            "output": {
                "markdown": markdown
            }
        }
    except Exception as e:
        logger.error(f"Error procesando documento: {str(e)}")
        return {"output": f"Error: {str(e)}"}
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

if __name__ == "__main__":
    # Escuchamos en 0.0.0.0:8000 para que el proxy de RunPod funcione correctamente
    uvicorn.run(app, host="0.0.0.0", port=8000)
