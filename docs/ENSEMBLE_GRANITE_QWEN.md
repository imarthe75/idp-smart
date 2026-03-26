# 🎯 Estrategia de Ensemble Híbrido: Qwen2-VL + IBM Granite

El motor de inteligencia de *Project Tolucón* abandona la inferencia monolítica (un solo modelo haciendo todo) para adoptar un esquema de **"Ensemble Híbrido" (Fusión Legal)**. 

Este enfoque divide la comprensión documental en **dos hemisferios cerebrales especializados**: uno puramente visual para el OCR y validación de firmas, y otro analítico robusto para forzar respuestas estrictas en JSON.

---

## 🧠 ¿Por qué abandonar Single-Model?

Durante los benchmarks con modelos de LocalAI (1.5B ~ 8B), quedó claro que:
1. **Modelos de Visión puros (Llava, Qwen-VL):** Son excelentes leyendo la geometría de la imagen y detectando sellos, firmas, logos y estructura física del expediente notarial. Sin embargo, su capacidad lógicamatemática para mapear docenas de folios y llaves UUID (como las del esquema `bi34.json`) degrada muy rápido y suelen devolver objetos `{}` o alucinar el JSON si el contexto crece.
2. **Modelos Instructivos Puros (Granite, Llama3):** Son herramientas analíticas formidables y manejan Gramática Estricta (GBNF) excepcionalmente bien, acatando el esquema JSON solicitado sin desvíos. El problema es que son ciegos; dependen de que un "OCR" previo les traduzca toda la imagen a un bloque de `Markdown` gigantesco que pierde contexto geométrico vital (como sellos sobre una línea).

---

## ⚙️ La Solución: Flujo "Fusión Legal" (Sequential Ensemble)

Para maximizar la precisión, el *Project Tolucón* ejecuta una cadena de mando (Sequential) a través del **Smart Router**:

### Fase 1: Extracción Visual Experta (Qwen2-VL 7B / 72B / Gemini 1.5)
1. El Celery Worker toma las páginas vitales del PDF (generalmente la primera hoja descriptiva, y la última hoja de firmas).
2. Usa la librería `PyMuPDF` para extraer las imágenes en alta fidelidad.
3. Se las envía al Modelo Visual (Qwen/Gemini) con un prompt determinista: *"Transcribe a nivel experto esta foja legal, extrayendo las firmas, tablas y sellos tal cual están estructurados geométricamente"*.
4. **Salida:** Un bloque de *Evidencia Visual Exacta*.

### Fase 2: OCR Estructural Rápido (Docling)
1. Para el resto de las 50-100 páginas del notariado que son mero texto (sin firmas críticas ni sellos superpuestos), el PDF se procesa localmente en CPUs mediante `Docling` a altísima velocidad.
2. **Salida:** Un documento masivo en formato `Markdown` estructurado de puro texto.

### Fase 3: Raking Lógico y Mapeo Estricto (IBM Granite 3.0 8B Instruct)
1. Se combinan las "Salidas" del Paso 1 + Paso 2 en un solo corpus.
2. El Orquestador inyecta este cuerpo gigante de contexto directamente al modelo razonador especializado (Granite).
3. Granite, que está configurado con temperatura `0.1` (alta rigidez determinista), evalúa la petición del esquema UUID del catálogo (e.g. `Traslativo de Domino BI1`).
4. Entendiendo matemáticamente el mapa JSON gracias a su entrenamiento corporativo, busca las referencias en la *Evidencia Visual* y el *Markdown Local*, entregando una representación estructurada JSON ultra-limpia sin alucinar y validando la presencia de las firmas extraídas de las imágenes.

---

## 🚀 Proyección y Configuración para Producción

En tu sistema, esto se regula transparentemente a través del contenedor `idp_api` y el backend `.env` (sea `runpod` o `vllm`).

**Beneficios frente al Single-Model:**
* **Velocidad:** Docling procesa 100 páginas en segundos localmente, liberando al GPU de "leer letras".
* **Rentabilidad:** Qwen2-VL solo es llamado para las 1-2 imágenes críticas, abaratando costos de inferencia multimodal prohibitivos (sea en tokens cloud o uso intensivo de VRAM).
* **Precisión:** Al liberar estrés visual de Granite, el LLM enfoca sus 8192 tokens de ventana de atención única y exclusivamente a resolver el "Rompecabezas" del JSON notarial contra el texto ya digerido.

## ⚖️ Alternativas a futuro (Adaptive Routing)

Si el `Smart Router` detecta en un futuro que un esquema no tiene firmas (`"has_signatures": false`), puede operar en un modelo **Adaptive**: Omitir completamente a Fase 1 (Qwen) y derivar únicamente a Docling + Granite, operando 100% On-Premise en la red de la oficina, costo cero.
