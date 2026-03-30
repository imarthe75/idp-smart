# Data Model: Expediente Multinivel

## Estructura Base (JSON Simplificado)
El resultado debe ser un JSON plano o semi-estructurado donde los arreglos se manejan de forma dinámica.

### Secciones Principales
1. **Notario**: Datos de la escritura, número, tomo, fecha.
2. **Enajenantes**: Arreglo de personas/entidades que venden.
3. **Adquirientes**: Arreglo de personas/entidades que compran.
4. **Inmueble**: Ubicación, cuenta catastral, medidas y colindancias.

### Manejo de Arreglos
Si existen múltiples elementos (ej. 3 enajenantes), el JSON debe contener un arreglo en la clave correspondiente:
```json
{
  "enajenantes": [
    {"nombre": "Persona A", "rfc": "..."},
    {"nombre": "Persona B", "rfc": "..."}
  ]
}
```

### Documentos Anexos
- Identificación de actas de nacimiento, CURPs, RFCs.
- Validación visual de sellos en cada anexo.

## Reglas de Validación
- Todo campo debe tener un UUID único asociado en el esquema de la forma.
- Si un valor no se encuentra, devolver `null` (no string vacío).
- Los arreglos deben integrarse en una sola pasada semántica.
