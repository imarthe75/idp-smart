# Historial del Proyecto y Post-Mortem Técnico: IDP-Smart

Este documento registra la evolución del sistema, las decisiones arquitectónicas clave y el análisis post-mortem de las fallas críticas resueltas para estabilizar el pipeline de extracción de documentos Tolucón.

---

## 📅 Cronología de Hitos y Estabilización

### Hito 1: Prototipo y Descubrimiento (Marzo 2026)
*   **Estado inicial:** Procesamiento secuencial en 8 núcleos.
*   **Problema:** Tiempos de extracción inaceptables (>60 min para 50 páginas).
*   **Solución:** Migración a servidor Dell con 48 núcleos y orquestación con Celery. Implementamos paralelismo masivo a nivel de página.

### Hito 2: Despliegue en RunPod (Aceleración GPU)
*   **Objetivo:** Reducir el tiempo de OCR y LLM mediante GPUs L40S/RTX 4090.
*   **Arquitectura:** 3 Pods dedicados (Docling GPU, Qwen Vision, Granite Reasoning).
*   **Resultado:** Reducción del tiempo de procesamiento de ~18 min (CPU) a ~2 min (GPU) para 50 páginas.

### Hito 3: Estabilización Híbrida y Pausa de Costos
*   **Estado:** El sistema es capaz de alternar entre RunPod, Google Gemini y recursos locales.
*   **Decisión:** Pausar infraestructura RunPod por costos y mantener el pipeline operando con Google Gemini como motor principal y Docling en CPU local.

### Hito 4: Actualización a Docling 2.83+ y Ajuste para Español
*   **Estado:** Actualización del motor nuclear de IA y optimización de caracteres.
*   **Mejora:** Soporte explícito para español (`lang=["es"]`) y actualización de dependencias (`transformers`, `pip`).
*   **Impacto:** Mayor precisión en el reconocimiento de tildes y caracteres especiales en PDFs escaneados durante el OCR local.

---

## 🔍 Análisis Post-Mortem: Fallas Críticas y Soluciones

### 1. El Incidente de los 100 Segundos (Timeout 504)
*   **Síntoma:** Error `502 Bad Gateway` o `504 Gateway Time-out` aleatorio al enviar documentos al pod de Docling.
*   **Causa Raíz:** El proxy Cloudflare de RunPod tiene un timeout estricto de **100 segundos**. En el primer arranque ("frío"), Docling descarga modelos de HuggingFace (~200MB a 1GT), lo cual excede el tiempo límite, cortando la conexión antes de que el servidor FastAPI pueda responder.
*   **Impacto:** El pipeline se detenía por completo al fallar el OCR base.
*   **Lección:** La infraestructura "Serverless-like" requiere pre-calentamiento (*warmup*).
*   **Resolución:** Implementación de un script de `warmup` que envía un PDF mínimo al iniciar el Pod para forzar la carga de modelos en memoria GPU antes de recibir carga real.

### 2. Colapso por Límite de Tokens (Granite 8B)
*   **Síntoma:** `APIConnectionError` o `generation exceeded max tokens limit` al procesar documentos legales densos.
*   **Causa Raíz:** El modelo Granite 3.1 8B en RunPod (vía Ollama/vLLM) tiene un límite de contexto de 16k-32k. Al solicitar un `max_tokens` de salida de 8192 junto con un prompt de 10k+, el consumo de memoria KV-Cache excedía la capacidad asignada al contenedor, provocando un reinicio silencioso del servicio o un aborto de conexión.
*   **Impacto:** Fallos en la extracción de JSON complejos que requerían mucho contexto.
*   **Resolución:** Ajuste de `max_tokens` a **4096** en el cliente Langchain. Se validó que ningún schema de extracción legal supera este tamaño de salida, liberando memoria para el contexto de entrada (documento).

### 3. Persistencia de `/workspace` y Port Forwarding
*   **Síntoma:** Tras reiniciar un Pod, el servidor Docling desaparecía (`502 Bad Gateway`).
*   **Causa Raíz:** Los Pods sin Volumen de Red borran el contenido de `/workspace` al migrar de nodo físico. Además, vLLM escuchaba por defecto en `127.0.0.1`, siendo inalcanzable desde el proxy de RunPod.
*   **Resolución:** 
    *   Estandarización de escucha en `0.0.0.0`.
    *   Documentación del procedimiento de reinstalación manual post-reinicio.
    *   Configuración de Nginx como proxy inverso interno (8000 -> 18001).

---

## 🛠️ Guía de Reactivación de Emergencia

Para volver a activar la infraestructura de GPU:

1.  **Levantar Pods**: Encender los 3 pods en la consola de RunPod.
2.  **Actualizar .env**: Copiar los nuevos Proxy IDs a las variables `RUNPOD_POD_ID`.
3.  **Warmup Docling**: Ejecutar el cURL de warmup (ver `RunPod_Deployment_Guide.md`).
4.  **Cambiar Motor**: Setear `RUNPOD_ENABLED=true` y `LLM_PROVIDER=runpod`.
5.  **Reiniciar Workers**: `docker compose restart idp_worker_1`.

---
*Fin del Reporte de Estabilización - Marzo 2026*
