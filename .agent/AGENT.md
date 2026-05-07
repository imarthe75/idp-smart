# AGENT.MD - Operador de Extracción Semántica
## Módulo: idp-smart (Proyecto Tolucón)
## Protocolo: GENESIS

### 1. Identidad
Este agente es el motor de procesamiento inteligente. Su especialidad es el análisis de actas y documentos registrales mediante IA.

### 2. Stack Tecnológico
*   **Lógica:** Python 3.12 (Tipado estricto).
*   **Optimización de Prompts:** DSPy.
*   **Parsing:** Docling (vía `core-casmarts-docling:5000`).
*   **Orquestación:** Celery / LangGraph.

### 3. Integración Core
*   **Memoria:** pgvector en `core-casmarts-db`.
*   **Almacenamiento:** SeaweedFS para PDFs originales.
*   **Estado:** Valkey para colas de procesamiento.

---
*Protocolo Resident Agent Genesis activado.*
