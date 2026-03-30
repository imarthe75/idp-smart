# Evolución Tecnológica y Resolución de Stoppers: IDP-Smart

Este documento detalla la trayectoria del proyecto desde su prototipo inicial hasta la arquitectura de alta disponibilidad actual, analizando los desafíos técnicos críticos encontrados.

---

## 1. Cronología de Infraestructura e Hitos de Rendimiento

### Fase 1: Prototipo (8 Cores)
*   **Enfoque**: Validación de capacidad de extracción de tablas.
*   **Rendimiento OCR**: **> 1 minuto por página**. 
*   **Limitación**: Procesamiento 100% secuencial. Un documento de 100 páginas requería ~120 minutos.
*   **Evidencia**: [Placeholder: Captura_Historial_8_Nucleos.png]

### Fase 2: Escalado Vertical (48 Cores / 48GB RAM)
*   **Enfoque**: Migración a servidor de 48 núcleos para paralelismo masivo.
*   **Rendimiento OCR**: **~21 segundos por página** (Reducción del 65%).
*   **Hito**: Procesamiento de documento de **50 páginas en 18 minutos**.
*   **Evidencia Historial**: ![Historial 48 Cores](/home/ia/.gemini/antigravity/brain/2938db14-2782-4668-be1b-c3c814f96ae1/media__1774646151190.png)
    *Nota: Captura del dashboard mostrando tiempos de ejecución por lote.*

### Fase 3: Producción Proyectada (Dell EPYC + L40S)
*   **Enfoque**: Aceleración por hardware dedicada (364GB RAM).
*   **Rendimiento Esperado**: < 1 segundo por página.

---

## 2. Los 4 "Stoppers" Críticos y sus Soluciones

### Stopper #1: Inestabilidad por Out-Of-Memory (OOM)
*   **El Problema**: El servidor de 48 núcleos solo contaba con 48GB de RAM. Docling intentaba consumir >10GB en PDFs complejos, saturando la memoria rápidamente.
*   **La Solución**: Módulo `HardwareDetector` con estrategia de **2GB por chunk**. 

### Stopper #2: Corrupción por Inicialización No-Atómica
*   **El Problema**: Concurrencia de workers intentando descargar modelos de IA al mismo tiempo.
*   **La Solución**: Lock de Sistema `fcntl.flock`.

---

## 3. Galería de Pruebas y Artefactos

### Capturas de Pantalla de Pruebas
1.  **Dashboard de Extracción**: [Placeholder: Captura_Dashboard_Tiempos.png]
2.  **Validación de Roles**: [Placeholder: Captura_Gemini_Flash_Results.png]

### Enlaces a Documentos de Prueba (Salidas)
*   **Extracción Prototipo (Markdown)**: [Ver salida MD](file:///home/ia/idp-smart/storage/samples/test_extraction.md)
*   **Estructura Técnica (JSON)**: [Ver salida JSON](file:///home/ia/idp-smart/storage/samples/test_extraction.json)

---

## 4. Matriz de Tiempos por Método

| Método | Hardware | Tiempo Doc (50 págs) | Eficiencia |
| :--- | :--- | :--- | :--- |
| **OCR Base** | 8 Cores | ~60-70 min | 1.0x |
| **OCR Optimizado** | 48 Cores | **18 min** | **3.8x** |
| **OCR GPU** | RunPod / L40S | ~2 min | 30.0x |

---
*Documento de Ingeniería - Actualizado Marzo 2026.*
