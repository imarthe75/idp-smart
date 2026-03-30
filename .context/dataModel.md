# Data Model: Expediente Multinivel

## Estructura del Expediente
El sistema procesa documentos bajo un modelo de **Expediente Multinivel**, donde un solo archivo o grupo de archivos puede contener múltiples secciones y anexos.

### 1. Secciones Principales
- **Notario**: Datos de la notaría, número de escritura, protocolo y sellos.
- **Enajenantes**: Lista de personas físicas o morales que venden/ceden. Incluye copropietarios y porcentajes de participación.
- **Adquirientes**: Lista de personas que compran/reciben.
- **Inmueble**: Datos del predio (Cédula Catastral, medidas, colindancias, ubicación).

### 2. Estructura de Datos (JSON)
```json
{
  "expediente_metadata": {
    "task_id": "uuid",
    "pages": 10
  },
  "secciones": {
    "notario": { "numero": 123, "estado": "QR" },
    "enajenantes": [
      {
        "nombre": "WILBERT GABRIEL",
        "porcentaje": 0.2,
        "es_copropietario": true
      }
    ],
    "adquirientes": [],
    "inmueble": {
      "clave_catastral": "801001000017003-1",
      "valor": 1500000.0
    }
  },
  "documentos_anexos": [
    { "tipo": "CEDULA_CATASTRAL", "letra_apendice": "G" }
  ]
}
```

### 3. Reglas de Integración
- **Arreglos Flexibles**: Enajenantes y Adquirientes deben ser arreglos, permitiendo N elementos sin romper el esquema.
- **Validación de Identidad**: Cruce de nombres entre el cuerpo de la escritura y los documentos anexos (Cédula, ID).
- **Consistencia Visual**: Los datos extraídos deben coincidir con los sellos y firmas detectados por el motor de visión.
