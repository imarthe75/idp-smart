# Comparativa Técnico-Financiera Integral (Carga Mensual: 15,000 expedientes / 750,000 páginas)

Este documento presenta un análisis exhaustivo de costos y rendimiento para todos los servicios propuestos, proyectando el gasto total mensual basado en una carga de 750,000 páginas.

---

## 1. Servicios de Extracción y OCR (Costos Mensuales)

| Proveedor | Servicio | Tiempo (pág/s) | Costo por Página | **Costo Total Mensual** |
| :--- | :--- | :--- | :--- | :--- |
| **Local (Actual)** | Docling CPU (48C/48GB) | 0.4 pág/s | $0.0000 | **$0 USD** |
| **RunPod** | Docling (L40S GPU) | 3.0 pág/s | $0.0002 | **$150 USD** |
| **AWS** | Textract (Standard) | 1.8 pág/s | $0.0150 | **$11,250 USD** |
| **Google** | Document AI | 2.1 pág/s | $0.0300 | **$22,500 USD** |
| **Azure** | AI Document Intelligence | 2.0 pág/s | $0.0100 | **$7,500 USD** |

---

## 2. Servicios de Inteligencia LLM (Costos Mensuales)
*Basado en un promedio de 8,000 tokens de input y 1,000 de output por expediente.*

| Modelo | Proveedor | Costo 1M Input | Costo 1M Output | **Costo Total Mensual** | Ventana Contexto |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Gemini 1.5 Flash** | Google | $0.075 | $0.30 | **$13.50 USD** | 1,000,000 |
| **Gemini 1.5 Pro** | Google | $1.250 | $5.00 | **$225.00 USD** | 2,000,000 |
| **Claude 3.5 Sonnet** | Anthropic | $3.000 | $15.00 | **$562.50 USD** | 200,000 |
| **GPT-4o** | OpenAI | $2.500 | $10.00 | **$487.50 USD** | 128,000 |
| **GPT-4o-mini** | OpenAI | $0.150 | $0.60 | **$27.00 USD** | 128,000 |
| **Granite 3.0** | IBM (Local) | $0.000 | $0.00 | **$0.00 USD** | 128,000 |

---

## 3. Comparativa de Infraestructura de Hardware

| Métrica | Servidor Orquestador Actual | Servidor Producción (Dell EPYC) |
| :--- | :--- | :--- |
| **Arquitectura CPU** | 48 Nucleos (Dual Socket) | Dual EPYC (96+ Nucleos) |
| **Memoria RAM** | **48 GB** (Limitado) | **364 GB** |
| **Memoria GPU** | N/A | **2x NVIDIA L40S (96GB VRAM)** |
| **Concurrencia Docling** | Media (Limitada por RAM) | Masiva (Acelerada por GPU) |
| **Costo Mensual (OPEX)** | ~$100 (Energía/Mantenimiento) | ~$300 (Energía/Enfriamiento) |
| **Estado Local** | Operativo | **Pendiente de Adquisición** |

---

## 4. Matriz Comparativa de Capacidades Técnicas

| Servicio | Manejo de Tablas | Análisis de Firmas | Ventana de Contexto | Dependencia Cloud |
| :--- | :--- | :--- | :--- | :--- |
| **Docling (Local)** | Excelente | Básico (vía LLM) | N/A | No |
| **AWS Textract** | Excelente | Muy Bueno | N/A | Sí |
| **Gemini 1.5 Flash** | No aplica | Bueno (Multimodal) | 1M Tokens | Sí |
| **Claude 3.5 Sonnet** | No aplica | Excelente | 200k Tokens | Sí |
| **IBM Granite** | No aplica | Básico | 128k Tokens | No |

---

## 5. Proyección de Volumen vs Tiempo

| Infraestructura | Tiempo por Expediente (Avg) | Días para 15,000 Expedientes |
| :--- | :--- | :--- |
| **Solo local (48C/48GB)** | 120 segundos | ~21 días (24/7) |
| **Híbrido (Local + RunPod)** | **25 segundos** | **~4.3 días (24/7)** |
| **Cloud Puro (AWS/Google)** | 45 segundos | ~7.8 días (24/7) |

---
*Nota: Los costos y tiempos son estimaciones basadas en los precios de lista a Marzo de 2026 y pruebas de rendimiento preliminares.*
