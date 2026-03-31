# 📊 Reporte de Análisis de Logs — Proyecto Tolucón

Tras analizar los logs de los pods (`logs (2).txt`, `logs (3).txt` y `logs (4).txt`), se han identificado los cuellos de botella y errores de configuración que están impidiendo que la infraestructura híbrida (RunPod + Servidor Local) funcione correctamente.

## 🔍 Hallazgos Principales

### 1. Desconexión de Red en vLLM (Granite & Qwen)
Los servidores de inferencia **Granite (Razonamiento)** y **Qwen (Visión)** están iniciando correctamente, pero están configurados de forma que el Orquestador no puede alcanzarlos:
*   **Problema de Host:** Los logs muestran que vLLM escucha en `127.0.0.1`. En un entorno de contenedores como RunPod, esto bloquea cualquier conexión externa. Debe escuchar en `0.0.0.0`.
*   **Conflicto de Puertos:** 
    *   **Qwen** está en el puerto `18001` (`logs (4).txt`).
    *   **Granite** está en el puerto `18000` (`logs (2).txt`).
    *   Sin embargo, el archivo `.env` y el `runpod_manager.py` esperan que ambos respondan en el puerto `8000` (puerto estándar del proxy HTTP de RunPod).

### 2. Pod de Docling (OCR) Incompleto
El log `logs (3).txt` muestra una instalación exitosa de dependencias, pero **ningún proceso de servidor se inicia**.
*   **Estado:** El pod termina su script de inicio tras el `pip install` y se queda "en espera" (idle) o se apaga, lo que causa que las peticiones de OCR fallen por timeout o conexión rechazada.
*   **Faltante:** No hay un script `handler.py` o un servidor FastAPI que reciba las peticiones de extracción base64 que el Orquestador envía.

### 3. Advertencias de Dependencias
Se observan conflictos de versiones en `pyzmq` y `fla` ops. Aunque no son fatales para el inicio del servidor, podrían causar inestabilidad bajo carga pesada o errores de precisión en el modelo Qwen.

---

## 🛠️ Plan de Acción (Qué sigue)

### Paso 1: Corregir Comandos de Inicio en RunPod
Es necesario actualizar el **Command Override** o el **Start Script** en los pods de RunPod con los siguientes comandos para asegurar visibilidad externa:

*   **Para el Pod de Granite (LLM):**
    ```bash
    python3 -m vllm.entrypoints.openai.api_server --model ibm-granite/granite-3.0-8b-instruct --host 0.0.0.0 --port 8000
    ```
*   **Para el Pod de Qwen (Vision):**
    ```bash
    python3 -m vllm.entrypoints.openai.api_server --model cyankiwi/Qwen3.5-27B-AWQ-4bit --host 0.0.0.0 --port 8000
    ```

### Paso 2: Implementar el Servidor Docling
Debemos crear un pequeño servidor API en el pod de Docling que soporte el endpoint esperado por el Orquestador. 

> [!TIP]
> Podemos usar un servidor simple con FastAPI para recibir el PDF en base64, procesarlo con Docling y devolver el Markdown. He creado el script `scripts/docling_server.py` para cumplir este propósito.

### Paso 3: Sincronizar `.env`
Asegurar que las URLs en el servidor local apunten correctamente a los proxies de RunPod:
```bash
# Ejemplo corregido
RUNPOD_LLM_URL=https://{POD_LLM_ID}-8000.proxy.runpod.net/v1
RUNPOD_VISION_URL=https://{POD_VISION_ID}-8000.proxy.runpod.net/v1
RUNPOD_DOCLING_URL=https://{POD_DOCLING_ID}-8000.proxy.runpod.net
```

### Paso 4: Pruebas de Conectividad
Una vez reiniciados los pods con la configuración correcta, ejecutar un script de validación desde el servidor local para confirmar que los 3 endpoints (OCR, Vision, Reasoning) responden correctamente antes de lanzar el pipeline masivo.

---

## 🚀 Próximos pasos técnicos
1. [ ] Crear el script `docling_server.py` para el pod de OCR. (Ya creado en `scripts/docling_server.py`)
2. [ ] Ajustar la configuración de `vLLM` para usar `0.0.0.0:8000`.
3. [ ] Realizar una prueba de extracción completa con un documento de muestra.
