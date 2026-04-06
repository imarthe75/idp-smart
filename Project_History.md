# Historial del Proyecto IDP-Smart

## 2026-03-04: Transición a Arquitectura Híbrida

### Cambios Principales
1. **Infraestructura AWS a Local**
   - Transición de 2 EC2 (Frontend + Backend gpu-worker g4dn.xlarge) a Servidor Local Unificado (Dell PowerEdge, 48 núcleos, 64GB RAM).
   - Eliminación de dependencias de AWS S3 y SQS.

2. **Nuevo Motor de Almacenamiento y Colas**
   - Se reemplazó S3 con **MinIO** local.
   - Se reemplazó SQS con **RabbitMQ** + Celery.
   - Se implementó Redis para manejo de estado global.

3. **Arquitectura de Microservicios**
   - `idp_api`: FastAPI para endpoints (subida, esquemas, estado, descargas).
   - `idp_worker`: Consumer Celery (escalado a 4 réplicas usando `--concurrency=4`).
   - `idp_dashboard`: UI de monitoreo en Streamlit.
   - `ollama`: Inferencia local (`granite3.1-dense:8b` y `qwen2.5:14b`).
   - `minio`, `rabbitmq`, `redis`: Servicios de infraestructura nativa.

4. **Flujo de Extracción Optimizado**
   - Nuevo pipeline en 3 pasos aislados en `agent.py`:
     1. OCR (PyMuPDF4LLM local - CPU)
     2. Visión Multimodal (Qwen a través de Ollama - Lento en CPU, opcional pero activado vía Ensemble)
     3. Razón/Extracción JSON (Granite 8B VLLM)

## 2026-03-31 a 2026-04-01: Integración Completa de GPUs y Estabilización de RunPod

### Resolución de Cuellos de Botella y Errores Críticos
1. **Fallo de Docling GPU (502 y 504 Timeout)**
   - Inicialmente, el pipeline original sufría *fallbacks* lentos ocultos a la CPU local (tomando más de 2-3 minutos por página) porque las peticiones chocaban con un timeout subyacente de 100 segundos del proxy Cloudflare de RunPod al cargar pesos en el primer request.
   - **Solución implementada**: Documentamos el proceso de despliegue en un entorno efímero dentro de RunPod (requiriendo volver a instalar dependencias y levantar `uvicorn` en puerto 18001 sobre el Pod vía SSH). Al descubrirse que el primer request de Docling debe cargar pesos en demanda y causar un timeout inevitable, se estipuló que un simple "warmup" (p. ej., ejecutando un test inicial de 1 página) carga todo en VRAM para que posteriores requests se resuelvan en <2s. Además, se desactivó permanentemente el fallback local (`DOCLING_RUNPOD_FALLBACK_TO_LOCAL=false`) forzando visibilidad en caso de caída global.
   
2. **Crash de Inferencia de LLM (Granite) - "generation exceeded max tokens limit" / "APIConnectionError"**
   - El worker falló repetidamente la fase analítica (75 segundos por documento, reportando timeout de Langchain). El pod remoto con vLLM estaba recibiendo peticiones con `max_tokens=8192` pre-seteado. La suma del "Prompt + output solicitado" desbordaba la config del contexto nativo de 16k tokens, induciendo un crash asincrónico que derivaba en falla TCP/HTTP.
   - **Solución implementada**: Se especificó el path exacto (`/v1`) en `RUNPOD_LLM_URL` y la etiqueta del modelo dentro de RunPod (`granite3.1-dense:8b`). Finalmente, se ajustaron configuraciones de red reescribiendo la instrucción de LLM en `agent.py` para demandar `max_tokens=4096`, evadiendo la interrupción y devolviendo predicciones estables de los JSON.

### Arquitectura Actual del Motor
- OCR Liviano Local (PyMuPDF) y OCR Analítico (Docling) redirigidos y balanceados globalmente a RunPod GPU (L4 / RTX4090).
- Tiempos de latencia por documento robustecidos: De tiempos híbridos inflados a >2m por página, transicionados a finalizado integral (visión + LLM) sub-secuencial de 10 a 20 segundos por documento promedio.

### 2026-04-01: Estabilización del Servidor Local (48 Cores / 48GB)

1. **Optimización de Concurrencia CPU (Bypass de RunPod)**
   - Tras detectar cuellos de botella por sobre-suscripción, se restauró la arquitectura de **4 Workers con pinning de CPU fijo** (12 núcleos por worker).
   - Se sintonizó el paralelismo a **3 lotes de 4 hilos cada uno** por worker, logrando una saturación perfecta de los 48 hilos.
   - Rendimiento recuperado: **~15s por página** (con picos de 1.25s/pág efectivos al procesar múltiples páginas en paralelo).

2. **Actualización de Motores y IA**
   - Migración a la API de **Docling 2.8+** (reemplazo de `ocr_options.use_gpu` por `accelerator_options`).
   - Integración de **Gemini 3.1 Flash Lite** con nueva API Key de alto rendimiento (**15 RPM / 500 RPD**), resolviendo errores de timeout y cuotas.
   - Refuerzo del _Prompt Legal_ para Recall Máximo, eliminando bugs de formateo JSON en la etapa AGENT.

### Próximos Pasos 
- Continuar el monitoreo de la RAM para evitar picos de Swap por encima de los 48GB durante el procesamiento de archivos de 1000+ páginas.
- Implementar cacheo persistente de resultados de visión en MinIO. (COMPLETADO 2026-04-06)

## 2026-04-06: Orquestación Multi-Cloud y Visión Avanzada (v3.3)

### 1. Motores OCR Multi-Cloud
- Implementación nativa de **Azure Document Intelligence**, **AWS Textract** y **Google Document AI** en `ocr_factory.py`.
- **Estrategia de Fallback Dinámico**: El orquestador intenta el motor configurado en `.env` (p. ej. Azure) y, ante cualquier fallo de cuota o red, realiza un *fallback* automático a **Docling local**, garantizando que la extracción nunca se detenga.

### 2. Visión Multimodal con Qwen2-VL
- Integración de análisis de evidencia visual mediante **Qwen2-VL** en RunPod.
- El sistema ahora detecta y describe sellos notariales, firmas autógrafas y logotipos, inyectando esta "Evidencia Visual" en el razonamiento del LLM.
- **Cacheo de Visión**: Se implementó un sistema de cacheo en MinIO que reutiliza análisis visuales de tareas previas basadas en el mismo documento, reduciendo costos de GPU y latencia.

### 3. Persistencia Dual TIFF/PDF
- Soporte extendido para archivos TIFF: El sistema ahora convierte automáticamente los TIFF a PDF de alta resolución.
- **Trazabilidad**: Ambos archivos (el TIF original y el PDF generado) se almacenan en MinIO bajo el ID de la tarea (`[task_id]/source_converted.pdf`), permitiendo auditoría completa.

### 4. Infraestructura y Conectividad
- **Acceso Externo a DB**: Apertura del puerto **5432** en el contenedor de Docker para permitir auditoría directa desde herramientas externas como Navicat.
- **Optimización de Segmentación**: Sintonización del `pdf_chunk_size` dinámico en `vision_optimized.py` para respetar los límites de RAM del hardware_detector.

### Próximos Pasos 
- Finalizar el motor de búsqueda semántica (RAG) sobre el repositorio histórico de MinIO.
- Desarrollar la interfaz "Human-in-the-loop" para validación masiva de campos de baja confianza.
