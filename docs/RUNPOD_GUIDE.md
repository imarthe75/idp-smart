# Guía de Despliegue en RunPod (Project Tolucón)

Este documento describe el procedimiento paso a paso para desplegar y utilizar la infraestructura en la nube de **RunPod** para el proceso de extracción documental híbrido del *Project Tolucón*. 

El objetivo principal de utilizar RunPod es externalizar las cargas pesadas de **Visión Computacional** (Docling OCR + Qwen2-VL) y de **Razonamiento LLM** (Granite), aprovechando GPUs potentes de manera efímera para pruebas o simulando el entorno de producción.

---

## 1. Requerimientos de Hardware (GPU)

Dado el tamaño de los modelos y la demanda computacional del procesamiento de documentos OCR, la selección de GPU es vital.

### Opciones Recomendadas Tiers
1. **NVIDIA L40S** *(Recomendado para Simulación Producción)*
   * Es la GPU equivalente al servidor Dell físico en producción.
   * Cuenta con 48 GB de VRAM, perfectos para sostener Qwen + Granite de manera holgada.
2. **NVIDIA RTX A5000 / RTX 5090** *(Recomendado para Pruebas/Desarrollo)*
   * **Requisito crítico:** Deben de contar con **al menos 32 GB de VRAM** u ofrecerán problemas de *Out Of Memory (OOM)*. 
   * Ideales por su excelente costo-beneficio para arrancar Pods efímeros en Desarrollo.

---

## 2. Creación del "Network Volume" (Vital)

Para evitar que los equipos de prueba de RunPod tengan que volver a descargar docenas de gigabytes de pesos de los modelos (Qwen y Granite) cada vez que el Pod se enciende y se apaga, debemos utilizar un **Network Drive persistente**.

### Paso a paso:
1. En el panel lateral izquierdo de RunPod, ve a **[Storage > Network Volumes]**.
2. Da clic en **Deploy Network Volume**.
3. **Location:** Elige la misma región donde vas a levantar tus Pods (EJ: `US - Secaucus`).
4. **Data Center:** Recomendado `SEC-1` o aplicable a tu volumen.
5. **Storage Size:** Configura **`50 GB`**. (Esto alberga al caché de Docling, a Qwen2-VL y a Granite 3.0 con holgura).
6. **Nombre:** `idp-models-cache`

### Rutas de Montaje:
Al configurar el Pod, asignaremos este Network Volume para que la persistencia se realice en las carpetas nativas de caché de Hugging Face de Linux.
*   **Path de Montaje:** `/root/.cache/huggingface`

Al enlazar el volumen a este path, `vLLM` y `Docling` guardarán (y buscarán) los modelos automáticamente ahí.

---

## 0. Configuración de API Key (Acceso Programático)
Para que el orquestador `idp-smart` pueda encender/apagar Pods y enviar tareas, requiere tu API Key secreta.

1.  Inicia sesión en **RunPod**.
2.  Ve a **[Settings > API Keys]**.
3.  Haz clic en **Generate Key**.
4.  > [!IMPORTANT]
    > **Copia la API Key inmediatamente.** Por motivos de seguridad, RunPod **no te permitirá volver a verla** una vez que cierres la ventana. Si la pierdes, deberás generar una nueva.

---

## 3. Configuración y Despliegue de los Pods

### 3.1. Pod Principal de Inferencia (Granite + Qwen2-VL)
Este se considera el **Pod Principal**. Es el encargado de alojar los modelos pesados (Hugging Face) y debe estar vinculado al "Network Volume" creado en el paso 2.

> [!WARNING]
> **No utilices la imagen `vllm/vllm-openai:latest` directamente en el campo "Image"** si deseas correr dos modelos simultáneos. Esa imagen tiene bloqueado el `ENTRYPOINT` nativo y provocará el error `vllm: error: unrecognized arguments: -lc`.

Para correr ambos modelos en paralelo en la misma tarjeta gráfica, debes usar una imagen base sin restricciones y encadenar el comando de arranque:

1. Ve a la pestaña **[Pods]** y da clic en **Deploy**.
2. **Selección de GPU:** Selecciona una **NVIDIA L40S (48GB)**. Es la única que garantiza estabilidad para ambos modelos de forma concurrente.
3. Elige el template oficial de **RunPod PyTorch** (ej. `runpod-torch-v240` o `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`).
4. Haz clic en **Customize Deployment** o en la edición del pod y configura estrictamente lo siguiente:

   - **Image:** `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`
   - **Container Start Command:** Copia y pega exactamente este comando:
     ```bash
     bash -lc "pip install vllm qwen-vl-utils && vllm serve ibm-granite/granite-3.0-8b-instruct --port 8000 --gpu-memory-utilization 0.45 --max-model-len 8192 & vllm serve Qwen/Qwen2-VL-7B-Instruct-AWQ --port 8001 --gpu-memory-utilization 0.45 --max-model-len 4096 & wait"
     ```
   - **Expose HTTP Ports:** `8000, 8001`
   - **Volume Mount Path:** `/root/.cache/huggingface` (Vinculado a tu Network Volume `idp-models-cache`).

> [!IMPORTANT]
> **¿Por qué 0.45?** Una GPU L40S tiene 48GB. Sin este flag, el primer proceso intentará acaparar el 90% de la VRAM, dejando al segundo proceso sin memoria. Al usar `0.45`, cada modelo reserva aprox. 21.6GB, permitiendo que ambos coexistan perfectamente y no choquen. Al agregar `&& wait`, nos aseguramos de que el pod mantenga ambos procesos vivos.

5. Una vez que inicie el Pod, RunPod generará dos URLs basadas en los puertos expuestos:
   - `https://[POD_ID]-8000.proxy.runpod.net` (Portal para Granite)
   - `https://[POD_ID]-8001.proxy.runpod.net` (Portal para Qwen2-VL)

---

## 4. Configurar el Aplicativo `idp-smart` (URLs de Inferencia)

Una vez que tu Pod esté en estado **"Running"**, obtén el **API ID** de la sección "Connect" y actualiza tu archivo `.env`:

```properties
# =============================================================================
# [1] ENDPOINTS DE INFERENCIA (RunPod Proxy)
# =============================================================================
# Incluir el sufijo /v1 al final de la URL del proxy
RUNPOD_LLM_URL=https://[TU_POD_ID]-8000.proxy.runpod.net/v1
RUNPOD_VISION_URL=https://[TU_POD_ID]-8001.proxy.runpod.net/v1
```

## 5. Gestión de Ciclo de Vida (Auto-Scaling)
Al activar `RUNPOD_ENABLED=true`, el Worker de *Project Tolucón* hará lo siguiente de manera autónoma:
1. **Despertar el Pod:** Si llega un documento nuevo y la máquina de RunPod está en reposo (Exited), el Worker llamará a la API GraphQL de RunPod para levantar el Pod.
2. **Ping Continuo:** Mientras el procesamiento de documentos se ejecuta, validará que el Pod mantenga la señal de vida (`touch`).
3. **Apagado Inteligente (Idle Shutdown):** Si tras `RUNPOD_IDLE_TIMEOUT` segundos la cola de documentos está vacía, el orquestador llamará a la API de RunPod para **detener** la máquina virtual. Esto garantiza ahorrar cientos de dólares, encendiendo el servidor remoto únicamente cuando hay expedientes.

---

## 5. Simulando Producción 

El ecosistema actual opera en **modo híbrido (Smart Router)**:

1. El orquestador Celery recibe la petición.
2. Extrae OCR estructural básico localmente con Docling(CPU).
3. Despierta RunPod mediante GraphQL.
4. Enruta las imágenes de firmas hacia Qwen2-VL en RunPod.
5. Pasa el documento concatenado hacia Granite 3.0 para su razonamiento en RunPod.
6. RunPod devuelve el JSON mapeado y, al quedarse sin carga, el orquestador detiene el Pod.

Si cuentas con la **L40S en tu sede On-Premise**, todo este proceso del router se configurará sobre `LLM_PROVIDER=vllm` apuntando a las IPs de la Red de Área local sin costo operativo de nube.
