# 🚀 Guía de Instalación Rápida (Project Tolucón)

Este documento describe los pasos EXACTOS para levantar el proyecto entero en un servidor completamente **NUEVO** o en un entorno de desarrollo en **menos de 5 minutos**. Al seguir al pie de la letra estas instrucciones, eludiremos cualquier problema de dependencias faltantes o errores de esquema de base de datos.

---

## 🛠️ Paso 0: Preparación del Entorno (Servidor Limpio)

Si estás en un servidor Linux (Ubuntu/Debian) recién instalado, ejecuta los siguientes comandos para instalar las herramientas base necesarias:

### 1. Instalar Git
```bash
sudo apt update && sudo apt install -y git
```

### 2. Instalar Docker y Docker Compose V2
```bash
# Instalar Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Instalar Docker Compose V2 (Si no está incluido)
sudo apt install -y docker-compose-v2

# Dar permisos al usuario actual (requiere cerrar sesión y volver a entrar)
sudo usermod -aG docker $USER
```

---

## 🛠️ Paso 1: Clonar el Repositorio

Descarga el código fuente del proyecto actualizado que incluye las refactorizaciones híbridas:

```bash
git clone https://github.com/imartinez-soportetd/idp-smart.git
cd idp-smart
```

---

## ⚙️ Paso 2: Configurar las Variables de Entorno (.env)

El proyecto incluye un archivo maestro de ejemplo en la raíz. Para un servidor nuevo, debes inicializar tu archivo `.env`.

```bash
# Copia el archivo de ejemplo a su versión definitiva
cp .env.example .env
```

### 🎯 Selección del Modo de Ejecución (LLM_PROVIDER)

Edita el archivo `.env` (`nano .env`) y elige uno de los 3 estilos de procesamiento configurando la variable `LLM_PROVIDER`:

#### **Opción A: Inferencia en la Nube (Google Gemini) - RECOMENDADO PARA INICIAR**
*Es el más rápido de configurar y no consume recursos de tu servidor.*
1. Coloca `LLM_PROVIDER=google`
2. Pega tu `GOOGLE_API_KEY=AIzaSy...`
3. Asegúrate que `RUNPOD_ENABLED=false`

#### **Opción B: Inferencia Híbrida/Nube (RunPod vLLM)**
*Ideal para alto rendimiento sin comprar servidores de $200k MXN.*
1. Coloca `LLM_PROVIDER=runpod`
2. Coloca `RUNPOD_ENABLED=true`
3. Pega tu `RUNPOD_API_KEY=...` y el `RUNPOD_POD_LLM_ID=...` de tu pod activo.
4. *Nota:* Consulta `docs/RUNPOD_GUIDE.md` para crear el Pod correctamente.

#### **Opción C: Inferencia Local (VLLM On-Premise)**
*Solo si tienes una GPU NVIDIA L40S o similar en tu red local.*
1. Coloca `LLM_PROVIDER=vllm`
2. Pega la IP de tu servidor local en `LOCAL_API_URL=http://10.4.3.23:8000`

---

## 🐳 Paso 3: Construcción y Despliegue con Docker

El sistema se encarga de autoconfigurar todo orgánicamente.

```bash
# Obligar la purga de Caché de dependencias y construir los contenedores
docker compose build --no-cache

# Levantar infraestructura en segundo plano
docker compose up -d
```

### ¿Qué hace mágico a este paso en un Servidor Nuevo?
- **Base de Datos Sana:** Postgres construye su volumen y procesa el esquema maestro original (`db/init-db.sql`) inyectando tablas de hardware y catálogos.
- **Librerías Frescas (Worker):** El contenedor instala `PyMuPDF` y `Docling` automáticamente.
- **Agnóstico de Hardware:** El detector de hardware integrado limitará los hilos de CPU dinámicamente para prevenir crasheos por falta de RAM.

---

## ✅ Paso 4: Verificación de Salud Integral

Asegúrate de que la infraestructura esté sana:

```bash
# Verificar visibilidad de contenedores
docker compose ps

# Debes ver 'Up' en: idp_db, idp_minio, idp_valkey, idp_api, idp_worker.
```

**Verificar los Logs del Worker**
```bash
docker logs -f idp_worker
```

---

## 🚀 Paso 5: ¡Listo para Procesar!

### Vía API Endpoint Directo (CURL)
```bash
curl -X POST http://localhost:8000/api/v1/process \
  -F "act_type=BI1" \
  -F "document=@tu_documento.pdf" \
  -F "json_form=@tu_esquema.json"
```

---

## ⚠️ Troubleshooting Rápido 

| Problema | Causa | Solución |
|----------|-------|----------|
| **Faltan columnas DB** | Servidor viejo con volumen viciado. | `docker compose down -v` y reiniciar. |
| **Timeout en Extracción** | `LLM_TIMEOUT` muy corto para modelos locales. | Aumentar `LOCAL_LLM_TIMEOUT=600` en `.env`. |
| **Worker Reporta OOM** | Falta de RAM para el motor OCR. | Configurar `DOCLING_CHUNK_SIZE=5` para procesar menos páginas a la vez. |
