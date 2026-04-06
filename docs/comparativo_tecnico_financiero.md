# Informe Maestro de Ingeniería y Estrategia Financiera: Proyecto Tolucón
**Escenario de Carga:** 15,000 expedientes/mes | 50 páginas promedio | 750,000 páginas totales/mes.

---

## 1. Matriz Maestra Full-Detail: Desglose Técnico-Financiero (11 Combinaciones)
Esta matriz detalla el costo operativo mensual segregado por componente, infraestructura y el total, incluyendo la inversión inicial (CAPEX) requerida para cada escenario.

| Motor de OCR (Extracción) | Cerebro de IA (LLM) | Infraestructura | Costo Motor (OCR) | Costo Cerebro (IA) | Costo Infra (Luz/Renta) | **Costo Inicial (CAPEX)** | **Total Mensual (OPEX)** | Eficiencia (Docs/Hora) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Docling (CPU 48C)** | **Gemini 1.5 Flash** | Híbrido Local/Cloud | $0.00 | $13.50 | $1,125.00 | $2,000.00 | **$1,138.50 USD** | 20 - 30 |
| **Docling (L40S Local)** | **Qwen2-VL / Granite** | **Soberanía (Local)** | **$0.00** | **$0.00** | **$300.00** | **$38,500.00** | **$300.00 USD** | **550 - 650** |
| **Document AI (GCP)** | **Gemini 1.5 Pro** | Cloud Nativo (GCP) | $22,500.00 | $225.00 | $100.00 | $1,500.00 | **$22,825.00 USD** | **1,200+** |
| **Document AI (GCP)** | **Qwen2-VL / Granite** | Híbrido Cloud/Local | $22,500.00 | $0.00 | $150.00 | $1,500.00 | **$22,650.00 USD** | 800 - 900 |
| **AWS Textract** | **Claude 3.5 Sonnet** | Cloud Nativo (AWS) | $11,250.00 | $562.50 | $100.00 | $1,500.00 | **$11,912.50 USD** | 800 - 1,000 |
| **AWS Textract** | **Granite 3.0 (Local)** | Híbrido AWS/Local | $11,250.00 | $0.00 | $150.00 | $1,500.00 | **$11,400.00 USD** | 600 - 700 |
| **Azure AI Doc** | **GPT-4o (Azure)** | Cloud Nativo (MS) | $7,500.00 | $487.50 | $100.00 | $1,500.00 | **$8,087.50 USD** | 900 - 1,100 |
| **Azure AI Doc** | **Granite 3.0 (Local)** | Híbrido Azure/Loc | $7,500.00 | $0.00 | $150.00 | $1,500.00 | **$7,650.00 USD** | 700 - 800 |
| **Docling (3x RunPod)** | **RunPod LLM (vLLM)** | Cloud IaaS (Renta) | $450.00 | $0.00 | $2,550.00 | $500.00 | **$3,000.00 USD** | 900 - 1,200 |
| **Docling (CPU 48C)** | **Claude 3.5 Sonnet** | Híbrido Local/Cloud | $0.00 | $562.50 | $1,125.00 | $2,000.00 | **$1,687.50 USD** | 20 - 30 |
| **Docling (CPU 48C)** | **Granite 3.0 (Local)** | Local Puro (No GPU) | $0.00 | $0.00 | $110.00 | $2,000.00 | **$110.00 USD** | **< 2 (Inviable)** |

---

## 2. Análisis Detallado de Inversión Inicial (CAPEX)
Especificaciones y costos del hardware de alta gama necesario para la producción masiva.

| Elemento de Hardware | Especificaciones Técnicas | Inversión Estimada |
| :--- | :--- | :--- |
| **Servidor Base Dell/Cisco** | Chasis Rack 2U con fuentes redundantes y Dual AMD EPYC | $12,500.00 USD |
| **Aceleración GPU** | **2x NVIDIA L40S (96GB VRAM Totales)** | $22,000.00 USD |
| **Memoria RAM** | **384GB DDR5 (24x 16GB o similar)** | $3,500.00 USD |
| **Almacenamiento NVMe** | 4TB Enterprise Gen4 para base de datos y cache | $500.00 USD |
| **Total Inversión (Soberanía Local)** | **Equipamiento para Producción de 15k Docs/mes** | **$38,500.00 USD** |

---

## 3. Análisis Granular de Costos de Inteligencia (LLM)
*Proyección basada en un promedio de 8,000 tokens de entrada y 1,000 de salida por cada expediente de 50 páginas.*

| Modelo | Proveedor | Costo 1M Input | Costo 1M Output | **Costo Total Mensual (15k docs)** | Ventana de Contexto |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Gemini 1.5 Flash** | Google | $0.075 | $0.30 | **$13.50 USD** | 1,000,000 |
| **GPT-4o-mini** | OpenAI / Azure | $0.150 | $0.60 | **$27.00 USD** | 128,000 |
| **Gemini 1.5 Pro** | Google | $1.250 | $5.00 | **$225.00 USD** | 2,000,000 |
| **GPT-4o** | OpenAI / Azure | $2.500 | $10.00 | **$487.50 USD** | 128,000 |
| **Claude 3.5 Sonnet** | Anthropic / AWS | $3.000 | $15.00 | **$562.50 USD** | 200,000 |
| **Granite 3.0 / Qwen** | **IBM (Local)** | **$0.000** | **$0.00** | **$0.00 USD** | 128,000 |

---

## 4. Desglose de Infraestructura y OPEX Mensual
Detalle de lo que compone la columna "Costo Infra" en la matriz maestra.

| Concepto | Servidor 48C Actual | **Dell/Cisco (2x L40S/384GB/EPYC)** | **3x RunPod (L40S)** |
| :--- | :--- | :--- | :--- |
| **Energía Eléctrica** | ~$80.00 USD | **~$220.00 USD** | N/A |
| **Enfriamiento / AACC** | ~$30.00 USD | **~$80.00 USD** | N/A |
| **Renta de Instancia** | N/A | N/A | **~$2,550.00 USD** |
| **Mantenimiento / Soporte** | Incluido | Amortizado | Incluido |
| **Total OPEX Mensual** | **$110.00 USD** | **$300.00 USD** | **$2,550.00 USD** |

---

## 5. Proyección de Costo Total de Operación (TCO) y ROI
*Proyección mensual para 15,000 expedientes incluyendo CAPEX + OPEX + Servicios.*

| Escenario de Despliegue | Inversión Inicial (CAPEX) | Gasto Mensual (OPEX) | **Costo Año 1 (Total)** | Costo por Expediente |
| :--- | :--- | :--- | :--- | :--- |
| **1. 100% On-Premise (L40S)** | **$38,500.00** | **$300.00** | **$42,100.00 USD** | **$0.020 USD** |
| **2. Híbrido (48C + Gemini)** | $2,000.00 | $1,138.50 | **$15,662.00 USD** | **$0.075 USD** |
| **3. RunPod (3 Pods)** | $500.00 | $3,000.00 | **$36,500.00 USD** | **$0.200 USD** |
| **4. Cloud Microsoft (Azure)** | $1,500.00 | $8,087.50 | **$98,550.00 USD** | **$0.539 USD** |
| **5. Enterprise (GCP)** | $1,500.00 | $22,825.00 | **$275,400.00 USD** | **$1.521 USD** |

---

## 6. Eficiencia de Tiempo y Memoria (Análisis de Producción)

1.  **Soberanía Local (Dual EPYC + 384GB RAM):** El factor crítico son los **384GB de RAM**. En el modelo de **Expediente Multinivel**, cada worker de Docling y cada proceso de LLM local podrá cargar el documento completo de 50 páginas en memoria ultrarrápida. Tiempo estimado para 15k expedientes: **~3.2 días**.
2.  **Infraestructura de 48C Actual:** Debido a los 48GB de RAM, el servidor se ve obligado a realizar *paging* (uso de disco como memoria), degradando la velocidad de procesamiento. Tiempo estimado: **~21 días**.
3.  **RunPod (3 Pods):** Ofrece la mayor elasticidad; al dividir la carga en 3, la concurrencia es masiva. Tiempo estimado: **~3.5 días**.

---

### Conclusión Estratégica
La implementación del servidor de alta gama (Escenario 1) es la única vía para alcanzar la **Soberanía Tecnológica** con un costo marginal de operación. El ahorro frente a GCP en el primer año (**$233,300 USD**) paga el servidor más de 6 veces. Este hardware es el cimiento necesario para que el frontend en **Angular** y la base de datos PostgreSQL operen sin latencia alguna mientras la IA procesa los expedientes en segundo plano.
