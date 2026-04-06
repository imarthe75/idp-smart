# Registro de Errores Críticos y Soluciones (IDP-Smart)

## 2026-04-01: Incidente de Degradación de Rendimiento y Errores de Extracción

### 1. Error de API en Docling (v2.83+)
- **Síntoma**: Docling reportaba 0 páginas procesadas (`page_count=0`) y terminaba en 3 segundos sin generar Markdown.
- **Causa**: Tras una actualización de software y una reversión de Git, el código utilizaba el campo `pipeline_options.ocr_options.use_gpu = False`, el cual fue eliminado en Docling 2.x.
- **Solución**: Se actualizó el motor `vision_optimized.py` para usar la nueva API: `pipeline_options.accelerator_options.device = "cpu"` y `num_threads = 4`.

### 2. Error de Formateo en Prompt de IA (Langchain)
- **Síntoma**: Error `Replacement index 0 out of range for positional args tuple` durante la etapa AGENT.
- **Causa**: El prompt legal contenía la cadena `([{...}])`. El motor de plantillas de Langchain interpretaba los corchetes con puntos como una variable de sustitución f-string y fallaba al no encontrar el argumento.
- **Solución**: Se escaparon las llaves dobles `([{{...}}])` en `agent.py` y se simplificó la invocación del LLM para evitar doble renderizado.

### 3. Sobre-suscripción de CPU (Slowdown 32s/pág)
- **Síntoma**: El tiempo de procesamiento por página subió de 16s a 32s (2x más lento).
- **Causa**: Configuración de 4 workers con `OMP_NUM_THREADS=12` cada uno en un servidor de 48 hilos. Esto provocaba que 48 hilos de computación pelearan por los mismos recursos, induciendo latencia por cambio de contexto.
- **Solución**: Se restauró la arquitectura de **4 Workers con pinning de CPU fijo** (12 núcleos por worker) y se ajustó el paralelismo interno a **3 lotes de 4 hilos cada uno** (`max_parallel_batches=3`, `num_threads=4`). Esto satura los 48 núcleos de forma ordenada sin colisiones.

### 4. Error 404 Gemini Model Not Found
- **Síntoma**: Fallo en la extracción con mensaje `model gemini-1.5-flash-latest not found`.
- **Causa**: Cambio manual de modelo en `.env` a una versión no soportada por la región/API Key en ese momento.
- **Solución**: Se unificó el uso de `gemini-3.1-flash-lite-preview` con una nueva API Key de alto rendimiento (15 RPM).

## 2026-04-06: Incidentes durante Mejora de Infraestructura

### 1. Error de Conexión en Workers (DB Restart)
- **Síntoma**: Tareas estancadas en `PENDING_CELERY` con el error `OperationalError: server closed the connection unexpectedly`.
- **Causa**: Se reinició el contenedor `idp_db` para exponer el puerto 5432 al host. Los workers de Celery mantuvieron conexiones TCP "muertas" (zombies) hacia la instancia anterior de la BD.
- **Solución**: Reinicio forzado de los 4 workers de Celery (`docker compose restart`) para refrescar el pool de conexiones. Se reseteó manualmente el estado de las tareas afectadas en la BD.

### 2. Error de Variable 'logger' No Definida en worker_app
- **Síntoma**: Falla inmediata al procesar TIFFs con error `NameError: name 'logger' is not defined`.
- **Causa**: Falta de importación o inicialización del logger en los nuevos bloques de persistencia de TIFF en `celery_app.py`.
- **Solución**: Se integró el logger estándar y se movió la lógica de persistencia al motor `vision_optimized.py` donde el contexto de logging es más robusto.
