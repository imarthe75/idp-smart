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

### 3.1. Pod de Inferencia Multimodal (Qwen2-VL / Granite)
Se utiliza de fábrica el Template oficial de vLLM, al cual le pasaremos el volumen y la configuración del servidor.

1. Ve a la pestaña **[Pods]** y da clic en **Deploy**.
2. **Selección de GPU:** Filtra y selecciona  `L40S` o  `A5000 (32+ VRAM)` o `RTX 5090`. Asegúrate de que el Datacenter sea el mismo del Network Volume.
3. Elige el template **`vLLM`** (oficial) de la categoría *Serverless* / *Community*.
4. **Volume Settings (Personalización):**
   * Elige tu Network Volume creado: `idp-models-cache`
   * Container Mount Path: `/root/.cache/huggingface`
5. **Environment Variables (Variables de Entorno):**
   En el apartado `Override Container Variables`, asegúrate de enviar los comandos a `vLLM` para descargar/exponer Qwen o Granite.
   
   **Ejemplo (Para levantar IBM Granite):**
   ```text
   MODEL_NAME=ibm-granite/granite-3.0-8b-instruct
   MAX_MODEL_LEN=8192
   ```

   **Ejemplo (Para levantar Vision Qwen2-VL):**
   ```text
   MODEL_NAME=Qwen/Qwen2-VL-7B-Instruct-AWQ
   ```

> **Nota:** En configuraciones avanzadas, puedes crear dos Pods distintos o consolidarlos, pero recuerda que el consumo de VRAM de ejecutar dos modelos grandes concurrentes requiere típicamente la L40S de 48GB.

---

## 4. Configurar el Aplicativo `idp-smart` (Smart Router)

Una vez que tu Pod de RunPod está inicializado ("Running") en la plataforma:

1. Ve al menú **Connect** en tu Pod y obtén el **API ID** al final de la URL, por ejemplo (`https://abcdefg...-8000.proxy.runpod.net/`).
2. Ve al panel de RunPod **[Settings]** > **API Keys** y obtén tu *Access Token*.

Con estos datos en mano, edita el archivo `.env` de tu clúster **idp-smart**:

```properties
# =============================================================================
# [4] RUNPOD — GESTIÓN DE CICLO DE VIDA (Power Control)
# =============================================================================
# Habilitar integración de RunPod auto-scaling / idle detection
RUNPOD_ENABLED=true
RUNPOD_API_KEY=tu_runpod_api_key_aqui

# ID Alfanumérico del Pod (visible en la sección "Connect" de RunPod)
RUNPOD_POD_LLM_ID=q032hxc9...

# Desconexión por inactividad. El agente idp-smart pausará el Pod tras 300 segundos de inactividad
RUNPOD_IDLE_TIMEOUT=300
```

Igualmente, configura el proveedor a RunPod en la Fase Activa:
```properties
LLM_PROVIDER=runpod
```

### Funciones del Agent Runpod Manager (`runpod_manager.py`)
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
