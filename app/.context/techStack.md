# Tech Stack: IDP Smart Notarial

## Infraestructura
- **Servidor**: 48 Cores / 48GB RAM.
- **Orquestación**: Celery con 8 workers dedicados.
- **Base de Datos**: PostgreSQL 15 + Redis (Valkey).

## Procesamiento de Documentos
- **OCR/Estructura**: Docling (Modo CPU optimizado).
- **Ajustes de Hilos**: `OMP_NUM_THREADS` limitado por worker para evitar sobrecarga de cambio de contexto.
- **Manejo de E/S**: MinIO para almacenamiento persistente de PDFs y resultados.

## Modelos de IA (Arquitectura de Puertos Duales)
- **Puerto 8000**: `ibm-granite/granite-3.0-8b-instruct` (Extracción Semántica).
- **Puerto 8001**: `Qwen/Qwen2-VL-7B-Instruct-AWQ` (Análisis Visual de Sellos/Firmas).

## Backend
- **Framework**: FastAPI.
- **Validaciones**: Pydantic v2 con reparación automática de JSON (Regex + LLM).
