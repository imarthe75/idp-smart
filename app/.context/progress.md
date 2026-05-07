# Progress: IDP Smart Notarial

## Estado Actual: Transición a Expediente Multinivel
- **Servidor 48 Cores**: Migración exitosa. Docker-compose configurado con 8 workers y afinidad de CPU.
- **Docling (OCR)**: Integrado con procesamiento por lotes (lógica Ultra-Fast).
- **IA Semántica**: Usando Granite-3.0 en puerto 8000. Lógica de reparación de JSON implementada.
- **IA Visual**: Motor Qwen2-VL preparado en puerto 8001 para lectura de sellos.

## Tareas Pendientes Inmediatas
1. **Robustez OCR**: Corregir error de colisión `[Errno 2]` en EasyOCR mediante bloqueo de proceso global.
2. **Validación de Arreglos**: Realizar pruebas intensivas con múltiples enajenantes/adquirientes.
3. **Visibilidad de Errores**: Asegurar que fallos en el agente se propaguen correctamente hasta la base de datos y UI.

## Historial de Cambios Recientes
- [2026-03-27] Implementación de validación estricta de JSON en el agente.
- [2026-03-27] Creación de sistema de documentación "Verdad Absoluta" en `.context/`.
- [2026-03-26] Optimización de afinidad de CPU (cpuset) para 8 workers.
