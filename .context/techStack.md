# Tech Stack: IDP Smart Notarial (Híbrido v2.0)

## Infraestructura
- **Servidor Orquestador Actual**: 48 Cores (Dual Socket) | **48GB RAM**.
- **Servidor Producción Objetivo**: Dell EPYC | 364GB RAM | 2x NVIDIA L40S (Pendiente).
- **Control de Recursos**: Gestión estricta vía `HardwareDetector` debido a la limitación de RAM actual (Estrategia 2GB/chunk).

## Capas de Procesamiento
1. **Extracción (OCR/Structuring)**:
   - **Local**: Docling (Modo CPU).
   - **Remote**: RunPod Docling API (Aceleración GPU).
   - **Mecanismo**: Fallback automático (Remote -> Local).

2. **Cerebro (LLM Orchestration)**:
   - **Principal**: Google Gemini 1.5 Flash.
   - **Fallbacks**: Anthropic Claude 3.5 Sonnet / OpenAI GPT-4o.
   - **Local LLM**: IBM Granite 3.0 / Qwen2-VL (Requiere GPU).

3. **Backend & Stack**:
   - **Core**: FastAPI + Celery.
   - **Estado**: PostgreSQL + Valkey (Redis).
   - **Seguridad**: Pydantic v2 (Configuración asíncrona).
