# 📚 Índice de Documentación - v3.0

**Estado:** Consolidada y limpia  
**Última actualización:** Marzo 20, 2026

---

## ✅ Documentación Activa (Mantener)

### 1. **README.md** ⭐ PRINCIPAL
   - Descripción general del proyecto
   - Arquitectura simplificada
   - Instalación rápida
   - Referencia de componentes y modelos

### 2. **OPTIMIZATION_DOCLING.md** 📊 TÉCNICO
   - Detalles de optimizaciones OCR
   - Benchmarks completos
   - Estrategias de paralelización, caching, detección de scaneados
   - Roadmap GPU/RunPod
   - Troubleshooting

### 3. **BENCHMARKING_GUIDE.md** 🏎️ RENDIMIENTO
   - Cómo correr mediciones de Docling (CPU vs GPU vs RunPod)
   - Scripts de `test_localai.py`
   - Uso de caches predictivos para acelerar pruebas locales

### 4. **QUICK_START.md** ⚡ OPERACIONAL
   - Guía 5 minutos (paso a paso)
   - Verificación de instalación
   - Medición de performance
   - Casos de uso específicos

### 5. **ENSEMBLE_GRANITE_QWEN.md** 🎯 ESTRATEGIA
   - Cuándo y por qué usar ensemble
   - Estrategias: sequential, parallel, adaptive
   - Configuración para produc ción
   - Comparativa con single-model

### 7. **RUNPOD_GUIDE.md** ☁️ INFRAESTRUCTURA
   - Guía de despliegue en RunPod (vía Network Volumes de 50GB)
   - Requerimientos de GPU (5090, A5000, L40S)
   - Configuración de Pods para Qwen2-VL y Granite
   - Integración con el Smart Router

### 8. **.env.example** ⚙️ CONFIGURACIÓN
   - Template oficial para despliegues limpios
   - Contiene la configuración para el router híbrido (Celery, VLLM, Gemini, Motores Locales)
   - Parámetros de detección de OOM y limitación de hilos

### 9. **CHANGELOG.md** 📝 HISTÓRICO
   - Cambios v1.0 → v2.0 → v3.0
   - Features nuevas
   - Mejoras de performance
   - Migraciones

---

## ❌ Documentación Obsoleta (Consolidada)

Los siguientes archivos han sido consolidados en la documentación activa arriba:

1. **ARQUITECTURA_LOCALAI.md**
   - ❌ ELIMINAR
   - ✅ Consolidado en: README.md sección "Arquitectura"

2. **LOCAL_EXECUTION_GUIDE.md**
   - ❌ ELIMINAR
   - ✅ Consolidado en: QUICK_START.md

3. **MIGRATION_GUIDE.md**
   - ❌ ELIMINAR
   - ✅ Consolidado en: CHANGELOG.md + README.md

4. **RUNPOD_INTEGRATION.md**
   - ❌ ELIMINAR
   - ✅ Consolidado en: OPTIMIZATION_DOCLING.md + .env.example

5. **DOCUMENTACION_COMPLETA_ARQUITECTURA.md**
   - ❌ ELIMINAR
   - ✅ Consolidado en: OPTIMIZATION_DOCLING.md + README.md

6. **CONFIGURACION_ENSEMBLE.md**
   - ❌ ELIMINAR
   - ✅ Consolidado en: ENSEMBLE_GRANITE_QWEN.md

7. **.env.example**
   - ❌ ELIMINAR
   - ✅ Consolidado en: .env.example

8. **.env.ensemble.examples**
   - ❌ CONSIDERAR ELIMINAR (redundante con .env.example
   - O mantener como referencia histórica

9. **QUICK_START.md**
   - ❌ ELIMINAR
   - ✅ Consolidado en: QUICK_START.md

10. **IMPLEMENTATION_SUMMARY.md**
    - ❌ ELIMINAR
    - ✅ Consolidado en: Este documento

---

## 📂 Estructura de Documentación Recomendada

```
idp-smart/
├── README.md                          # PRINCIPAL - Empezar aquí
├── CHANGELOG.md                       # Histórico de cambios
│
├── QUICK_START.md            # ⚡ Instalación 5 min
├── OPTIMIZATION_DOCLING.md           # 📊 Detalles OCR
├── ENSEMBLE_GRANITE_QWEN.md          # 🎯 Estrategia LLM
├── .env.example
│
├── FORM_SCHEMA_GUIDE.md              # Guía de esquemas JSON
│
└── [ELIMINAR]:
    ├── ARQUITECTURA_LOCALAI.md       ❌
    ├── LOCAL_EXECUTION_GUIDE.md      ❌
    ├── MIGRATION_GUIDE.md            ❌
    ├── RUNPOD_INTEGRATION.md         ❌
    ├── DOCUMENTACION_COMPLETA_ARQUITECTURA.md  ❌
    ├── CONFIGURACION_ENSEMBLE.md     ❌
    ├── QUICK_START.md                ❌
    ├── .env.example                  ❌
    ├── .env.ensemble.examples        ⚠️  (opcional)
    └── IMPLEMENTATION_SUMMARY.md     ❌
```

---

## 🎯 Cómo Usar Esta Documentación

### Para Usuarios Nuevos
1. Leer: **README.md** (visión general)
2. Ejecutar: **QUICK_START.md** (pasos 1-6)
3. Probar: Procesar un documento PDF
4. Si problemas → Ver QUICK_START.md sección Troubleshooting

### Para Ingenieros/DevOps
1. Leer: **README.md** (arquitectura)
2. Explorar: **OPTIMIZATION_DOCLING.md** (detalles técnicos)
3. Configurar: **.env.example
4. Referencia: **CHANGELOG.md** (cambios recientes)

### Para Producción
1. Seleccionar scenario en **.env.example
2. Configurar según **OPTIMIZATION_DOCLING.md**
3. Activar Ensemble si necesario: **ENSEMBLE_GRANITE_QWEN.md**
4. Monitorear: Ver logs en QUICK_START.md

### Para Actualizaciones Futuras
1. GPU disponible → Actualizar `VISION_DEVICE=cuda` (README.md)
2. RunPod listo → Activar `DOCLING_RUNPOD_ENABLED=true` (README.md)
3. Ensemble en prod → Poner `USE_ENSEMBLE=true` (ENSEMBLE_GRANITE_QWEN.md)

---

## 🔄 Flujo de Documentación Recomendado

```
Usuario abre proyecto
    ↓
Lee README.md → Entiende qué es y cómo funciona
    ↓
Ejecuta QUICK_START.md → Instala en 5 min
    ↓
¿Necesita optimizar?
    ├─ Sí → Lee OPTIMIZATION_DOCLING.md
    └─ No → Ya funciona
    ↓
¿Quiere máxima precisión?
    ├─ Sí → ENSEMBLE_GRANITE_QWEN.md
    └─ No → Granite solo está bien
    ↓
¿Necesita GPU o RunPod?
    ├─ GPU → .env.example
    ├─ RunPod → .env.example
    └─ CPU → .env.example
    ↓
¿Algo no funciona?
    └─ QUICK_START.md → Troubleshooting
```

---

## 📊 Matriz de Decisiones

### ¿Qué documentación necesito?

| Situación | Documento |
|-----------|-----------|
| Acabo de llegar | README.md |
| Quiero instalar rápido | QUICK_START.md |
| Tengo error | QUICK_START.md Troubleshooting |
| OCR demasiado lento | OPTIMIZATION_DOCLING.md |
| Quiero GPU | OPTIMIZATION_DOCLING.md + .env.example
| Quiero RunPod | OPTIMIZATION_DOCLING.md + .env.example
| Necesito máxima precisión | ENSEMBLE_GRANITE_QWEN.md |
| ¿Qué cambió? | CHANGELOG.md |
| Entiendo JSON schemas | FORM_SCHEMA_GUIDE.md |

---

## ✨ Beneficios de Esta Consolidación

✅ **Menos confusión** - Una fuente de verdad (README.md)  
✅ **Más fácil actualizar** - Documentos especializados, no redundantes  
✅ **Onboarding rápido** - Nuevos usuarios no pierden tiempo buscando  
✅ **Escalable** - Fácil agregar docs nuevas sin conflictos  
✅ **Mantenible** - Un único flujo de documentación  

---

## 🎬 Acción Recomendada

### Ahora Mismo
1. ✅ README.md está actualizado
2. ✅ QUICK_START.md listo
3. ✅ OPTIMIZATION_DOCLING.md completo
4. ✅ .env.example
5. ✅ ENSEMBLE_GRANITE_QWEN.md disponible

### Próximas 24 Horas
- [ ] Revisar y eliminar archivos obsoletos
- [ ] Actualizar cualquier referencia interna
- [ ] Comunicar a equipo el nuevo índice

### Próxima Semana
- [ ] Crear guía de contribución si es necesario
- [ ] Actualizar cualquier script de setup
- [ ] Consolidar más si es necesario

---

**Documentación v3.0 - Consolidada, Limpia, Lista para Producción** ✨
