# 🏢 IDP-Smart Project Context

## 🎯 Visión General
IDP-Smart (Intelligent Document Processing) es una plataforma industrial de extracción de datos para el sector notarial y registral. Su objetivo es transformar documentos legales complejos (escrituras, actas, contratos) en datos estructurados (JSON) con precisión quirúrgica.

## 🏗️ Factores Críticos de Éxito
1. **Precisión Literal:** No resumir nombres o cargos. (Ej: "JORGE A." != "JORGE ALFONSO").
2. **Estructura Técnica:** Los datos deben inyectarse en un esquema JSON predefinido basado en UUIDs.
3. **Escalabilidad:** Procesar de 1 a 500 páginas mediante workers distribuidos (Celery).
4. **Seguridad:** Protección de PII (Datos de identidad, montos, direcciones).

## 🛠️ Stack Tecnológico
- **Frontend:** HTML/JS/CSS (Dashboard de Benchmarking).
- **Backend:** FastAPI (Python).
- **OCR:** Docling (Procesamiento de PDF a Markdown estructurado).
- **IA:** Multimodal (Gemini 1.5 Flash/Lite, Claude 3.5 Sonnet).
- **Infraestructura:** Docker, MinIO (S3), PostgreSQL, Valkey (Redis).

## 📄 Tipos de Documentos (Benchmark)
- **BI3:** Actos de una sola página (Contenido denso).
- **BI20:** Actos de múltiples hojas (10+ páginas, contexto disperso).
