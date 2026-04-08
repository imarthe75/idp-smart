# Guía de Integración: Vertex AI en IDP-Smart

Esta guía detalla los pasos necesarios para migrar el motor de razonamiento de **Google AI Studio (API Key)** a **Vertex AI (GCP Enterprise)**.

---

## 1. Configuración en Google Cloud Platform (GCP)

### Paso 1: Crear Proyecto y Habilitar APIs
1. Ve a la [Consola de GCP](https://console.cloud.google.com/).
2. Crea un nuevo proyecto (ej: `idp-notarial-smart`).
3. Habilita las siguientes APIs:
   - **Vertex AI API**
   - **Cloud Storage API**

### Paso 2: Crear Cuenta de Servicio (Security First)
1. Ve a **IAM y administración > Cuentas de servicio**.
2. Crea una cuenta llamada `idp-smart-worker`.
3. Asígnale los siguientes roles:
   - `Vertex AI User` (Para invocar modelos).
   - `Storage Object Viewer` (Para leer PDFs de los buckets).
4. Ve a la pestaña **Claves**, crea una nueva clave **JSON** y descárgala.
5. Guarda este archivo en el servidor de IDP-Smart (ej: `/home/ia/idp-smart/keys/service-account.json`).

### Paso 3: Crear Bucket de Staging
1. Ve a **Cloud Storage > Buckets**.
2. Crea un bucket (ej: `idp-smart-staging`).
3. Asegúrate de que la cuenta de servicio creada tenga permisos sobre este bucket.

---

## 2. Configuración en IDP-Smart

Actualiza tu archivo `.env` con los datos obtenidos en el paso anterior:

```bash
# Motor Principal
LLM_PROVIDER="vertex"
GEMINI_MODEL="gemini-1.5-flash-002"

# Datos GCP
GCP_PROJECT_ID="idp-notarial-smart"
GCP_LOCATION="us-central1"
GCP_STAGING_BUCKET="idp-smart-staging"

# Autenticación
GCP_CREDENTIALS_JSON="/app/keys/service-account.json"
```

---

## 3. Instalación de Dependencias

En el entorno del worker o servidor, instala la librería necesaria:

```bash
pip install langchain-google-vertexai
```

---

## 4. Flujo de Trabajo (PDF Nativo)

Cuando `LLM_PROVIDER` es `vertex`, el sistema habilita el procesamiento multimodal. Los pasos técnicos que realiza IDP-Smart son:

1. **Carga**: Sube el PDF al bucket de staging en GCP.
2. **Referencia**: Genera una URI `gs://idp-smart-staging/documento.pdf`.
3. **Inferencia**: Invoca a Gemini enviando la URI y el esquema de extracción (Modo "Solo Valores").
4. **Respuesta**: Recibe el JSON plano y lo mapea automáticamente a las etiquetas notariales.
