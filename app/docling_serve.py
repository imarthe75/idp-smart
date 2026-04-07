from fastapi import FastAPI, HTTPException
from pydantic_settings import BaseSettings
import os
import uvicorn
from typing import Optional
from engine.vision_optimized import DoclingVisionOptimized
import logging

# Configuración básica para el servidor
class ServeSettings(BaseSettings):
    port: int = 8001
    host: str = "0.0.0.0"

settings = ServeSettings()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docling-serve")

app = FastAPI(title="Docling Service")

# Inicializar motor local
vision_engine = DoclingVisionOptimized()

@app.get("/health")
def health():
    return {"status": "ok", "device": vision_engine.profile.docling_device}

@app.post("/extract")
async def extract(bucket: str, object_name: str):
    """
    Recibe un objeto de MinIO y lo procesa localmente con Docling.
    """
    try:
        logger.info(f"📄 Procesando: {bucket}/{object_name}")
        # Re-utilizamos la lógica optimizada que ya tiene chunking y detección de hardware
        markdown, total_pages, strategy = vision_engine.extract_markdown_from_minio_sync(object_name)
        
        return {
            "markdown": markdown,
            "total_pages": total_pages,
            "strategy": strategy,
            "device": vision_engine.profile.docling_device
        }
    except Exception as e:
        logger.error(f"❌ Error en extracción: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # Con 40 cores y 80GB RAM, podemos tener 4 trabajadores de Docling en paralelo
    # Cada uno manejará un documento usando 10 hilos OpenMP (4*10 = 40)
    uvicorn.run("docling_serve:app", host=settings.host, port=settings.port, workers=4)
