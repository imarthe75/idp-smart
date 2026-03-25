# Informe de Diagnóstico Técnico: LocalAI vs Google Gemini

**Fecha:** 24 de Marzo de 2026  
**Estatus del Proyecto:** v1.1 - Optimización de IA y Trazabilidad

## 1. Resumen de Hallazgos
Durante la fase de integración de inferencia local con LocalAI, se detectó una discrepancia significativa en la calidad de la extracción de datos entre los modelos de la infraestructura local y el motor de Google Gemini.

| Característica | LocalAI (Modelos 1.5B/Tiny) | Google Gemini (Pro 1.5) |
| :--- | :--- | :--- |
| **Resultados** | `simplified_json: {}` (Campos Vacíos) | Extracción Completa (>95%) |
| **Causa Raíz** | Baja Capacidad de Razonamiento | Alta Capacidad Multimodal |
| **Velocidad** | Muy rápida (< 20s) | Moderada (30-60s) |
| **Confiabilidad** | Nula para esquemas complejos | Alta para producción |

---

## 2. Análisis del Fallo en LocalAI
### ¿Por qué devolvió JSON vacío?
A pesar de conectar exitosamente con el backend de LocalAI, los modelos utilizados para las pruebas de ligereza (`Qwen-1.5B-Instruct` y `Granite-4.0-Tiny-Q4`) fallaron al procesar los documentos por las siguientes razones:

1.  **Complejidad del Esquema Registral:** El sistema solicita la extracción de más de **60 campos únicos** basados en **UUIDs**. Un modelo de 1.5B o menos parámetros no tiene la "ventana de atención" ni la capacidad lógica suficiente para mapear el texto extraído a un esquema tan ramificado.
2.  **Strict Mode (GBNF Parser):** Para asegurar que el API no falle, se usa gramática GBNF que fuerza al modelo a seguir un esquema JSON estricto. Cuando los modelos pequeños no encuentran una relación clara entre el texto y el esquema, prefieren devolver un objeto vacío `{}` en lugar de inventar (alucinar) datos, lo que resulta en extracciones técnicamente "exitosas" pero vacías de información.
3.  **Contexto OCR:** Los documentos notariales generan archivos Markdown extensos. Al aumentar el tamaño del contexto (`context_size: 16384`), el modelo gasta la mayor parte de su capacidad computacional simplemente "viendo" el texto, dejando poco espacio para el "razonamiento" de extracción.

---

## 3. Ventajas de Google Gemini en el Pipeline
Tras las pruebas comparativas, se ha determinado que Gemini es el motor de producción ideal por:
*   **Mapeo Predictivo:** Entiende jerarquías complejas y puede relacionar descripciones legales con campos técnicos sin perderse.
*   **Manejo de Adendas:** Su ventana de contexto masiva le permite comparar múltiples documentos (documento principal + adendas) sin degradar la precisión.
*   **Costo de Hardware:** Al ser un servicio gestionado (Cloud), no consume los recursos de CPU/RAM del servidor IDP, liberando potencia para el motor OCR (Docling).

---

## 4. Próximos Pasos para Inferencia Local
Si se desea retomar LocalAI con éxito en el futuro, se recomienda:
*   Mínimo **8B parámetros** (Llama 3 o Granite 8B Instruct).
*   Hardware con al menos **24GB de VRAM** (GPU dedicada) para manejar el contexto sin timeouts.
*   Uso exclusivo para tareas de menor complejidad o pre-clasificación de documentos.

---
**Firmado:**  
*Equipo de Ingeniería IDP-Smart*
