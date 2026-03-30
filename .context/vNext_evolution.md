# vNext Evolution: idp-smart v4.0

Este documento centraliza las propuestas arquitectónicas y funcionales para la siguiente gran iteración del sistema, enfocadas en soberanía de datos, escalabilidad masiva y un modelo de datos orientado a expedientes complejos.

## 1. Migración de Infraestructura: SeaweedFS
Sustitución de MinIO por **SeaweedFS** para cumplir con normativas de licenciamiento y optimizar el rendimiento.

### Objetivos:
- **Licenciamiento Apache 2.0**: Eliminar la dependencia de AGPLv3 (MinIO).
- **Optimización de Metadatos**: Uso de volúmenes para manejar millones de archivos pequeños (thumbnails, chunks de texto, JSONs) sin degradar el sistema de archivos.
- **Webhooks Reactivos**: Configurar `Filer` con notificaciones HTTP hacia el API de FastAPI para disparar el procesamiento IDP.

---

## 2. Nuevo Modelo: Expediente Digital Multinivel (Dossier)
Evolución del modelo actual (un solo PDF) a un sistema de **Expediente Consolidado**.

### Estructura Propuesta:
- **Dossier/Expediente**: El contenedor principal (ej. "Escritura 4501").
- **Secciones Dinámicas**:
    - **General**: Documentos base.
    - **Sección Legal**: Escrituras, actas constitutivas.
    - **Sección Identidad**: IDs, CURP, RFC.
    - **Sección Inmuebles**: Cédulas catastrales, planos.
- **Subida Granular**: 
    - Permitir subir archivos directamente a una sección específica del expediente.
    - El procesamiento IDP debe ser capaz de "unir" (merge) la información extraída de diferentes secciones en un único JSON maestro del expediente.
    - **Acumulación de Datos**: Si se sube una adenda a la sección "Legal", el sistema debe actualizar el JSON del expediente sin perder lo ya extraído.

## 3. Robustecimiento Inmediato (v3.2+)
Acciones críticas para la versión actual antes del salto a v4.0:

- **Estrategia de Reintentos (Retries)**: Implementar reintentos inteligentes con backoff exponencial para las llamadas a LLM (Gemini/RunPod) para manejar errores 503/504.
- **Validación Estricta**: Reforzar la validación de entrada en el API para asegurar que los esquemas JSON de las formas sean válidos antes de encolar.
- **Auditoría de Procesamiento**: Guardar logs detallados de *por qué* un campo falló la validación de Pydantic o el mapeo semántico.
- **Health Check Reforzado**: Incluir estado de VRAM y CPU en el endpoint de salud para prevenir cuellos de botella.

---

## 4. Inteligencia Avanzada y Observabilidad (Roadmap v4.0+)
Integración de capacidades adicionales para transformar el sistema en una plataforma de inteligencia legal completa:

- **RAG (Legal Intelligence)**: Motor de búsqueda semántica (ChromaDB/Qdrant) para interrogación de expedientes históricos y cruce con jurisprudencia local. Facilitará auditorías transversales y consultas en lenguaje natural sobre el repositorio completo.
- **Human-in-the-loop (HITL) & Self-Tuning**: Interfaz de validación humana para extracciones de baja confianza. Incluye un bucle de retroalimentación donde el prompt se auto-ajusta dinámicamente según las correcciones humanas para maximizar la precisión futura.
- **Telemetry & Observability**: Implementación de un monitor de salud con Prometheus y Grafana para el seguimiento en tiempo real de latencia, uso de recursos (GPU/VRAM) y tasa de éxito de extracción.

---

## 5. Otros Pendientes Consolidados
- **Motor de Fusión Legal**: Refinar la lógica que decide entre el OCR de Docling y la visión de Qwen2-VL en caso de discrepancias en sellos/fechas.
- **Dashboard de Control**: Vista de administrador para ver el estado de los 15,000 archivos en tiempo real (Pendiente, Procesando, Validado, Error).
- **Modelo de Lenguaje Local**: Transición completa a Granite 3.0 / Qwen 2.5 local para eliminar dependencia de APIs externas en servidores soberanos.
