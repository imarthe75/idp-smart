# Reporte de Validación: Estabilización de RunPod

## Resumen de Pruebas Realizadas
Se diseñó un script (`scripts/reprocess_failed.py`) para re-encolar automáticamente las extracciones que fallaron originalmente debido a *timeouts* en el pod de Docling o problemas de longitud de contexto en Granite (LLM).

## Tareas Evaluadas
* **Tarea 1**: `019d4641-5300-7c7d-88c6-600bcbbaf5cc` (13 páginas)
* **Tarea 2**: `019d466f-4b36-7393-9db1-62a161321284` (2 páginas)

## Errores Originales y su Diagnóstico
1. **Error 504 - Gateway Timeout en Docling**
   - **Diagnóstico**: La llamada a `https://l8xuwyqajbqpw8-8000.proxy.runpod.net/...` superaba el límite de 100 segundos del Cloudflare Proxy de RunPod en documentos pesados, lo que derivaba en un fallo y gatillaba innecesariamente el fallback a CPU local que tardaba horas. Además, se constató que el pod de Docling devolvía "504" instantáneo si el servidor interno `uvicorn` estaba caído o no había sido inicializado.
   - **Corrección**: Se deshabilitó permanentemente el fallback falso a CPU local configurando `DOCLING_RUNPOD_FALLBACK_TO_LOCAL=false`. Esto fuerza a fallar rápido si RunPod no está online y visibiliza el error. Se dictó que se debe forzar una carga inicial (warmup) del modelo OCR en memoria antes de recibir peticiones grandes.

2. **Error `generation exceeded max tokens limit` en Granite LLM**
   - **Diagnóstico**: Múltiples páginas de markdown se concatenaron enviando el prompt a vLLM hasta requerir un límite (max_tokens=8192) que colisionaba con los topes de contexto inferidos para la versión de `granite3.1-dense:8b` (que oscilaba hasta truncar sockets TCP).
   - **Corrección**: Se modificó `app/engine/agent.py` para reducir `max_tokens=4096`, garantizando la estabilidad de la respuesta sin desbordamientos de memoria de GPU ni la interrupción asincrónica del servicio Ollama/vLLM. El endpoint de Granite está respondiendo `200 OK` actualmente y su integración está en verde.

## Estado Actual de la Infraestructura
* **Servicio LLM**: Funcional. Modelos cargados respondiendo en tiempos de 9-11s.
* **Microservicios (Celery/RabbitMQ)**: Funcionales y despachando rápidamente tareas re-encoladas al recibir webhook de MinIO o llamado manual a `/reprocess`.
* **Servicio Docling (Visión)**: **CAÍDO (Down)**. Está arrojando *504 Gateway Time-out*. Al revisar la terminal remota de RunPod, se detectó que el comando de inicialización de *uvicorn* falló indicando `cd: /workspace/idp-smart: No such file or directory`. 

## Resolución Inmediata Requerida
El servicio Docling en RunPod debe reinicializarse. Para solventarlo, es estrictamente necesario acceder al Pod de Docling (`l8xuwyqajbqpw8`) y clonar el repositorio internamente ya que fue borrado en ningún reset o nunca fue transferido en el pod actual. 

**Comandos a ejecutar dentro del Pod Docling (vía Terminal Web o SSH):**
```bash
cd /workspace
git clone https://github.com/imartinez-soportetd/idp-smart.git
cd idp-smart
pip install --no-cache-dir fastapi uvicorn pydantic python-multipart
nohup uvicorn scripts.docling_server:app --host 127.0.0.1 --port 18001 > /workspace/docling.log 2>&1 &
```
Una vez ejecutados, las extracciones fallidas podrán procesarse de nuevo.
