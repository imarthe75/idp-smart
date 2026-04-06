# Guía de Despliegue y Solución de Problemas (RunPod)

Esta guía documenta los pasos críticos, problemas comunes y soluciones descubiertas durante la integración del pipeline híbrido de IDP-Smart con los pods de IA en RunPod.

## 1. Pod de Docling (OCR GPU)
**ID del Pod:** `l8xuwyqajbqpw8`
- **Error Común:** `502 Bad Gateway` al reiniciar el Pod.
  - **Causa:** Si el Pod no tiene un Volumen de Red adjunto, el directorio `/workspace` se borra en los reinicios/migraciones. El servidor de FastAPI `docling_server.py` deja de estar presente y Nginx no tiene hacia dónde apuntar.
  - **Solución:** Conectarse vía Proxy SSH y levantar el servicio manualmente:
    ```bash
    ssh -o StrictHostKeyChecking=no l8xuwyqajbqpw8-64411763@ssh.runpod.io -i ~/.ssh/id_ed25519
    cd /workspace && git clone https://github.com/imartinez-soportetd/idp-smart.git && cd idp-smart
    pip install --no-cache-dir docling fastapi uvicorn pydantic python-multipart
    pkill -f uvicorn
    nohup uvicorn scripts.docling_server:app --host 127.0.0.1 --port 18001 > /workspace/docling.log 2>&1 &
    ```
- **Error Común:** `504 Gateway Time-out` (con o sin fallback activado).
  - **Causa:** El proxy Cloudflare de RunPod corta las conexiones inactivas a los **100 segundos** exactos. En frío, Docling tarda más de 100 segundos en descargar los modelos de HuggingFace, provocando el colapso del request HTTP.
  - **Solución:** Ejecutar un *Warmup* manual pasando un PDF vacío mínimo por cURL antes de procesar tareas reales:
    ```bash
    curl -X POST "https://l8xuwyqajbqpw8-8000.proxy.runpod.net/" -H "Content-Type: application/json" -d '{"input": {"pdf_base64": "JVBERi0xLjQKJcOkw7zDtsOfCjIgMCBvYmoKPDwvTGVuZ3RoIDMgMCBSL0ZpbHRlci9GbGF0ZURlY29kZT4+CnN0cmVhbQp4nDPQM1Qo5ypUMFAwALJMLY30jE2VDBWMlSyg4hZcIQpdK1yLglzcXN3cItzCQ4JdXYM83INdglxcXN39XVzcfXy8XANc3f0hYgD/mRGHCmVuZHN0cmVhbQplbmRvYmoKCjMgMCBvYmoKNDQKZW5kb2JqCgo0IDAgb2JqCjw8L1R5cGUvUGFnZS9NZWRpYUJveFswIDAgNTk1IDg0Ml0vUmVzb3VyY2VzPDwvRm9udDw8L0YxIDEgMCBSPj4+Pi9Db250ZW50cyAyIDAgUi9QYXJlbnQgNSAwIFI+PgplbmRvYmoKCjEgMCBvYmoKPDwvVHlwZS9Gb250L1N1YnR5cGUvVHlwZTEvQmFzZUZvbnQvSGVsdmV0aWNhPj4KZW5kb2JqCgo1IDAgb2JqCjw8L1R5cGUvUGFnZXMvQ291bnQgMS9LaWRzWzQgMCBSXT4+CmVuZG9iagoKNiAwIG9iago8PC9UeXBlL0NhdGFsb2cvUGFnZXMgNSAwIFI+PgplbmRvYmoKCnhyZWYKMCA4CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDI1OCAwMDAwMCBuIAowMDAwMDAwMDE1IDAwMDAwIG4gCjAwMDAwMDAxMTYgMDAwMDAgbiAKMDAwMDAwMDEzNyAwMDAwMCBuIAowMDAwMDAwMzQ2IDAwMDAwIG4gCjAwMDAwMDA0MDUgMDAwMDAgbiAKMDAwMDAwMDQ1NCAwMDAwMCBuIAp0cmFpbGVyCjw8L1NpemUgOC9Sb290IDYgMCBSL0luZm8gNyAwIFI+PgpzdGFydHhyZWYKNTE5CiUlRU9GCg=="}}'
    ```

## 2. Pod de LLM Analítico (Granite 8B)
**ID del Pod:** `80exc9n5fr5awt`
- **Error Común:** `404 Not Found` o fallos de conexión Langchain.
  - **Causa 1:** El contenedor está ejecutando Ollama y el puerto expuesto al exterior por la plantilla de RunPod es el `11434`, no el `8000`.
  - **Causa 2:** El nombre del modelo definido en Ollama es exactamente `granite3.1-dense:8b`. Cualquier otro nombre genera un 404 del modelo no encontrado al llamar al endpoint de chat.
  - **Solución:** Configurar correctamente `.env` con:
    ```env
    RUNPOD_LLM_URL=https://80exc9n5fr5awt-11434.proxy.runpod.net/v1
    LLM_RUNPOD_MODEL=granite3.1-dense:8b
    ```
- **Error Común:** `generation exceeded max tokens limit` / `APIConnectionError`.
  - **Causa:** Granite tiene un contexto truncado (16384 o menor dependiendo de la plantilla Ollama de RunPod). Si se especifica un `max_tokens` (output) muy alto (ej. 8192) y se suma un prompt moderadamente grande (ej. 9000), se excede el límite máximo de memoria permitida y VLLM/Ollama colapsa o aborta la conexión. Con el timeout por default (60s), provoca un APIConnectionError falso.
  - **Solución:** Reducir `max_tokens` en el cliente de Langchain a `4096`. El JSON de extracción jamás requerirá un output tan inmenso.

## 3. Pod de Visión / Multimodal (Qwen)
**ID del Pod:** `1o0foms1c1mlqq`
- Se accede a través de `https://1o0foms1c1mlqq-8000.proxy.runpod.net/v1`. Testeado y operativo por defecto sin problemas detectados.

## Políticas de Prevención Post-Mortem:
Se desactivó `DOCLING_RUNPOD_FALLBACK_TO_LOCAL=false` en el entorno para asegurar que los fallos persistentes del túnel de RunPod reporten errores fatalmente *inmediatamente*, en vez de absorber la sobrecarga local por los timeouts de 100 segundos.

## 4. Transición a Entorno Local (Pausa de Pods)
Cuando los Pods se detengan para ahorrar costos, es importante reconfigurar el archivo `.env` para volver al motor local o cloud (Google Gemini / vLLM local). 

**Cambios en `.env`:**
```env
# Desactivar integraciones RunPod
RUNPOD_ENABLED=false
LLM_PROVIDER=google # o 'vllm' si tienes el worker de L40S activo.
DOCLING_RUNPOD_FALLBACK_TO_LOCAL=true # Para forzar OCR con recursos locales
```
*No olvides reiniciar los workers tras guardar el archivo:*
`docker compose restart idp_worker_1 idp_worker_2 idp_worker_3 idp_worker_4 idp_api`

**Para reactivar RunPod:**
1. Enciende los Pods en la consola de RunPod.
2. Verifica los IDs y puertos.
3. Asegúrate de clonar y levantar el servidor de Docling en el pod (Paso 1).
4. Cambia `RUNPOD_ENABLED=true` y `LLM_PROVIDER=runpod` en `.env`.
5. Reinicia los workers.

## 5. Mantenimiento Avanzado y Optimización Local
Para asegurar la mayor precisión en el OCR local (español):
- El motor Docling se ha actualizado a la versión **2.83+** con dependencias de visión actualizadas.
- Se ha configurado `ocr_options = EasyOcrOptions(lang=["es"])` en `ocr_factory.py` para mejorar el reconocimiento de caracteres especiales.
- Siempre que se realicen cambios en las dependencias nucleares (`requirements.txt` o `Dockerfile`), es mandatorio reconstruir la imagen:
  ```bash
  docker compose build --no-cache api worker_1 worker_2 worker_3 worker_4
  docker compose up -d
  ```
