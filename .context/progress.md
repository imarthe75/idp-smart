# Progress: IDP Smart Notarial

## Milestones Completados ✅
- **Setup Inicial**: Estructura de FastAPI + Celery + Redis.
- **Integración Docling**: OCR local funcional para PDFs estructurados.
- **Escalabilidad de CPU**: Optimización para servidor de 48 núcleos (NUMA aware).
- **Hibridación Remota**: Cliente RunPod integrado para procesamiento paralelo masivo.
- **Autoadaptación**: Módulo `HardwareDetector` para ajuste dinámico de recursos.
- **Resiliencia Pydantic**: Corrección de modelos de configuración para entornos productivos.

## En Curso 🚧
- **Validación Visual**: Integración de Qwen2-VL local para detección de sellos oficiales.
- **Dashboard de Usuario**: Visualización de progreso y tiempos de respuesta por documento.
- **Optimización de Memoria**: Refinamiento de la estrategia de 2GB/chunk bajo carga extrema.

## Próximos Pasos 🎯
- **Nodo Inferencia L40S**: Despliegue del segundo nodo con GPUs dedicadas.
- **Detección de Entidades**: Refinamiento de prompts para distinguir roles complejos (Donante vs Donatario).
- **Batch Processing**: Interfaz para carga masiva de los 15,000 expedientes.
