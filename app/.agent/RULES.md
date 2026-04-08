# 📜 Global Rules (Reglas Globales) v2

Estas reglas optimizan la eficacia y reducen alucinaciones siguiendo los instintos de ECC.

## 🧠 Ciclo de Razonamiento (CoT)
1. **Analizar Contexto:** Antes de extraer, identifica el tipo de acto (Compraventa, Donación, etc).
2. **Pensamiento Crítico:** Escribe un breve análisis en una sección de "PENSAMIENTO" antes del JSON.
3. **Validación de Evidencia:** Solo extrae datos que tengan evidencia textual clara. Si el dato es ambiguo, márcalo como null.

## 📋 Reglas de Extracción Quirúrgica
- **Preservación de Entidades:** Extraer nombres de personas físicas y morales exactamente como aparecen, incluyendo errores ortográficos si existen en el original.
- **Mapeo de UUID:** Solo usar los UUIDs proporcionados en el esquema. No inventar llaves nuevas.
- **Tratamiento de Nulos:** Si una sección del esquema no aplica al documento actual, devolver el objeto con valores null en lugar de omitirlo.
- **Formato:** El resultado final debe ser un bloque JSON válido dentro de triple comillas invertidas.

## 🛡️ Seguridad y Calidad
- **No Alucinación:** Si el documento es un TIF o PDF con poco texto, no supongas datos por el nombre del archivo.
- **PII Integrity:** Mantener la integridad de RFCs, CURPs y números de escritura.
- **Output Cleanliness:** El modelo no debe pedir disculpas ni dar explicaciones fuera de la sección de PENSAMIENTO.
