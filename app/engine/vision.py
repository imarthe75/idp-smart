"""
Vision Engine - OCR con Docling
Este módulo ahora usa vision_optimized para máxima performance
"""

import asyncio
from engine.vision_optimized import extract_markdown_from_minio


# DEPRECATED: Función anterior mantenida para compatibilidad
# Usa la versión optimizada automáticamente
async def extract_markdown_from_minio_legacy(object_name: str) -> str:
    """
    ⚠️ DEPRECATED: Usa extract_markdown_from_minio() en su lugar
    Esta función se mantiene solo para compatibilidad
    """
    return await extract_markdown_from_minio(object_name)

