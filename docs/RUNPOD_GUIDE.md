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

## 3. Configuración y Despliegue de los Pods

### 3.1. Pod de Inferencia Multimodal Dual (Granite + Qwen2-VL)
Para el **Proyecto Tolucón**, es **OBLIGATORIO** cargar ambos modelos simultáneamente para permitir la orquestación de Visión + Razonamiento Legal.

1. Ve a la pestaña **[Pods]** y da clic en **Deploy**.
2. **Selección de GPU:** Selecciona una **NVIDIA L40S (48GB)**. Es la única que garantiza estabilidad para ambos modelos.
3. Elige el template **`vLLM`** (oficial) o un contenedor base de PyTorch 2.4+.
4. **Comandos de Inicio (Terminal):**
   Deberás abrir dos terminales dentro del Pod o usar un script de inicio (`entrypoint.sh`) para levantar ambos servicios:

   ```bash
   # Terminal 1: Granite 3.0 (Puerto 8000 - Razonamiento / JSON)
   vllm serve ibm-granite/granite-3.0-8b-instruct --port 8000 --gpu-memory-utilization 0.45 --max-model-len 8192

   # Terminal 2: Qwen Vision (Puerto 8001 - Visión / Sellos)
   vllm serve Qwen/Qwen2-VL-7B-Instruct-AWQ --port 8001 --gpu-memory-utilization 0.45 --max-model-len 4096
   ```

> [!IMPORTANT]
> **¿Por qué 0.45?** Una GPU L40S tiene 48GB. Sin este flag, el primer proceso intentará acaparar el 90% (~43GB), dejando al segundo proceso sin memoria. Al usar `0.45`, cada modelo reserva aprox. 21.6GB, permitiendo que ambos coexistan en la misma GPU de 48GB (total ~43.2GB + margen para sistema).

5. **Configuración de Red (Expose Ports):**
   RunPod permite exponer múltiples puertos mediante su sistema de Proxy Inverso inteligente. Para que `idp-smart` funcione, debes:
   - Configurar en la sección **Expose HTTP Port** (o Network Settings) del Pod los puertos **8000** y **8001**.
   - RunPod generará dos URLs basadas en el puerto:
     - `https://[POD_ID]-8000.proxy.runpod.net` (Granite)
     - `https://[POD_ID]-8001.proxy.runpod.net` (Qwen2-VL)

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
