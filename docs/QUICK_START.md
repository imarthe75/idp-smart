# 🚀 Guía de Instalación Rápida (Project Tolucón)

Este documento describe los pasos EXACTOS para levantar el proyecto entero en un servidor completamente **NUEVO** o en un entorno de desarrollo en **menos de 5 minutos**. Al seguir al pie de la letra estas instrucciones, eludiremos cualquier problema de dependencias faltantes o errores de esquema de base de datos.

## 📋 Requisitos Previos

- Servidor Linux (Ubuntu 22.04+ recomendado) o M2/M3 si es Mac.
- **Docker** y **Docker Compose V2** instalados.
- Git instalado.
- *(Opcional)* NVIDIA Drivers y `nvidia-container-toolkit` instalados si se correrán los modelos localmente con GPU de alta gama.

---

## 🛠️ Paso 1: Clonar el Repositorio

Descarga el código fuente del proyecto actualizado que incluye las refactorizaciones híbridas:

```bash
git clone https://github.com/imartinez-soportetd/idp-smart.git
cd idp-smart
```

---

## ⚙️ Paso 2: Configurar las Variables de Entorno

El proyecto incluye un archivo maestro de ejemplo en la raíz. Para un servidor nuevo, debes inicializar tu archivo `.env`.

```bash
# Copia el archivo de ejemplo a su versión definitiva
cp .env.example .env
```

**Variables Críticas a modificar**
Edita tu nuevo archivo usando `nano .env` y revisa al menos estas credenciales vitales:
1. `GEMINI_API_KEY`: Sólo si asignarás `LLM_PROVIDER=google` para balancear o respaldar (Fallback) la extracción multimodal en la nube de GCP.
2. `RUNPOD_API_KEY` y `RUNPOD_POD_LLM_ID`: Totalmente necesarios si vas a emplear el motor VLLM de la nube con encendido automático. (Ver *RUNPOD_GUIDE.md*).

*El resto de variables (claves DB, credenciales Redis, MinIO) ya tienen valores predeterminados funcionales de fábrica.*

---

## 🐳 Paso 3: Construcción y Despliegue con Docker

El sistema se encarga de autoconfigurar todo orgánicamente (Bases de datos con catálogos poblados, colas Valkey/Redis, API FastAPI Rápida y Múltiples Workers Celery).

```bash
# Obligar la purga de Caché de dependencias y construir los contenedores
docker compose build --no-cache

# Levantar infraestructura en segundo plano
docker compose up -d
```

### ¿Qué hace mágico a este paso en un Servidor Nuevo?
- **Base de Datos Sana:** Postgres construye su volumen y procesa el esquema maestro original (`db/init-db.sql`). Este script inyecta la tabla `hardware_benchmarks` con sus exclusivas columnas de detección de memoria (`oom_detected`, `processing_unit`) que evitaron dolores de cabeza del pasado, además de volcar un catálogo de ochenta y ocho actos pre-cacheados.
- **Librerías Frescas (Worker):** Tu worker instalará todo lo pactado en `requirements.txt` de manera transparente, inyectando `PyMuPDF` nativo sin que requieras hacerlo manual.
- **Agnóstico de Hardware:** El módulo nativo en la imagen evaluará dinámicamente cuánta RAM y Cores posee tu host y limitará Docling para evadir crasheos.

---

## ✅ Paso 4: Verificación de Salud Integral

Asegúrate de que la infraestructura esté sana:

```bash
# Verificar visibilidad de contenedores
docker compose ps

# Debes ver el estado de 'Up / Running' en:
# - idp_db (PostgreSQL - Puerto 5433)
# - idp_minio (Almacenamiento S3 Falsa Carga - Puerto 9000/9001)
# - idp_valkey (Redis Cache - Puerto 6379)
# - idp_api (FastAPI Core Server - Puerto 8000)
# - idp_worker (Celery / Background Async Tasks)
```

**Verificar los Logs del Worker (Vital para asegurar extracción visual)**
```bash
# Mira los logs del worker para asegurar que se conectó a Celery y detectó hardware
docker logs -f idp_worker
```

---

## 🚀 Paso 5: ¡Listo para Procesar!

Con la API en línea, puedes procesar tu primer PDF o solicitar una Extracción Híbrida. 

### Vía API Endpoint Directo (CURL)
```bash
curl -X POST http://localhost:8000/api/v1/process \
  -F "act_type=BI1" \
  -F "document=@tu_documento.pdf" \
  -F "json_form=@tu_esquema.json"
```

El router inteligente atrapará el acto. Si es multimodal, tomará fotos de las hojas, dividirá el batch a CPU local y lanzará las peticiones duras al orquestador asignado (`Google`, `Runpod`, `LocalAI`).

---

## ⚠️ Troubleshooting Rápido 

| Problema / Mensaje de Error | Causa | Solución |
|-----------------------------|-------|----------|
| **Faltan columnas (Ej. oom_detected)** | Levantaste en un servidor viejo cuyo volumen DB ya estaba viciado o desactualizado. | Haz `docker compose down -v` y elimina `idp_db_data`. Se borrarán pruebas viejas y nacerá sana. |
| **Timeout Agent / Celery se cuelga** | Host sobrecargado en modo Local o `LLM_TIMEOUT` muy corto para modelos Granite nativos. | Edita `.env` e incrementa el `LLM_TIMEOUT` a >`500`. Valida RunPod o Gemini como fallback confiable. |
| **Worker Reporta OOM / Killed** | Tu máquina no soporta PyTorch CPU para modelos grandes de Visión local. | Configurar `DOCLING_THREADS=1` o derivar al Cloud activando el `Smart Router`. |
