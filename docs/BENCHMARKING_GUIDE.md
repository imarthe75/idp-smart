# Docling Vision Engine Benchmarking Guide
## Mide Performance y Proyecta Escalabilidad (CPU → GPU → RunPod)

**Propósito:** Capturar baseline de performance actual (CPU) para comparar con GPU/RunPod en el futuro.

**⚠️ IMPORTANTE:** El benchmarking corre **DENTRO del Docker container** donde están todas las dependencias. No es necesario instalar nada en el host.

---

## 🐳 Prerequisitos

El benchmarking necesita que los servicios Docker estén ejecutándose:

```bash
cd idp-smart

# Verificar que Docker está corriendo
docker ps | grep idp_app

# Si NO está ejecutándose, inicia
docker compose up -d
```

---

## 🚀 Quick Start

### 1. Quick Test (1 minuto - Recomendado primero)
```bash
cd idp-smart

# Ejecuta benchmark dentro de Docker
bash scripts/run_benchmark.sh quick

# Espera ~1 minuto y verás resultados
```

### 2. Standard Test (3-5 minutos)
```bash
bash scripts/run_benchmark.sh standard
# Ejecuta 3 documentos de 5 páginas cada uno
```

### 3. Full Test (15-20 minutos - Completo)
```bash
bash scripts/run_benchmark.sh full
# Ejecuta todos los tests + cache test
```

---

## 📊 Output & Interpretación

### Ejemplo de Output:

```
================================================================================
📊 BENCHMARKING RESULTS SUMMARY
================================================================================

🔍 Test: batch_test_1_of_3
--------------------------------------------------------------------------------
  Documents tested: 1
  Total pages: 5
  Time - Min: 8.23s, Max: 8.23s, Avg: 8.23s
  Throughput - Min: 0.61, Max: 0.61, Avg: 0.61 pgs/s
  Std Dev: 0.00s

🔍 Test: batch_test_2_of_3
--------------------------------------------------------------------------------
  Documents tested: 1
  Total pages: 5
  Time - Min: 7.89s, Avg: 7.89s
  Throughput - Min: 0.63 pgs/s

================================================================================
📈 PROYECCIONES DE ESCALABILIDAD
================================================================================

📊 Performance Actual (CPU):
  Throughput: 0.62 páginas/segundo
  Tiempo por documento (10 pgs): 16.1s
  Documentos por hora: 225
  Documentos por día (24h): 5400

🚀 Proyecciones CON GPU NVIDIA (RTX 3060+):
  Throughput estimado: 3.72 páginas/segundo (6×)
  Tiempo por documento (10 pgs): 2.7s
  Documentos por hora: 1350
  Documentos por día (24h): 32400

☁️ Proyecciones CON RUNPOD Serverless:
  Throughput estimado: 8.00 páginas/segundo
  Tiempo para 100 páginas: 12.5s
  Documentos por hora: 2880

================================================================================
COMPARACIÓN PERFORMANCE: CPU vs GPU vs RunPod
================================================================================

Scenario                  Time            Pages/min           Status
-----------------------------------------
CPU (TODAY)               16.1s            37.3                ✅ Active now
GPU (Q2 2026)            2.7s             222                 6-7× faster (when added)
RunPod (Q3 2026)          3.3s             182                 Distributed, scalable
GPU + Cache HIT          0.2s            2700                 99× faster (repeated docs)

================================================================================

✅ Benchmark complete!
```

### Interpretación:

| Métrica | Significa | Impacto |
|---------|-----------|--------|
| **Throughput (pgs/sec)** | Páginas procesadas por segundo | Mayor = mejor escalabilidad |
| **Time/doc** | Tiempo para procesar 10 páginas | Menor = más rápido |
| **Docs/hour** | Capacidad por hora | Multiplicar por 24 para capacidad diaria |
| **Std Dev** | Variación en timing | Menor = más consistente |

---

## 🎯 Uso Avanzado

### Test Específico (tu PDF)
```bash
bash scripts/run_benchmark.sh custom /path/to/your/document.pdf

# Ejemplo real
bash scripts/run_benchmark.sh custom /ruta/al/documento.pdf
```

### Test Cache
```bash
# Mide speedup en documentos repetidos (debería ser ~100× más rápido)
bash scripts/run_benchmark.sh cache
```

### Extended Test (Todas las cargas)
```bash
# 5 docs de 5 pgs + 3 docs de 10 pgs + 2 docs de 20 pgs
bash scripts/run_benchmark.sh extended
```

### Análisis Manual (Python)
```python
import pathlib
import csv

# Leer resultados CSV
results_dir = pathlib.Path("benchmark_results")
csv_file = list(results_dir.glob("benchmark_*.csv"))[0]

with open(csv_file) as f:
    reader = csv.DictReader(f)
    for row in reader:
        print(f"{row['document_name']}: {float(row['time_elapsed']):.2f}s")
```

---

## 📈 Interpretar para tu Caso

### Si tu throughput es < 0.5 pgs/sec
```
⚠️  Performance baja - Posibles causas:
- CPU muy lenta (< 8 cores)
- Memoria insuficiente (< 16GB)
- Disco muy lento (¡SSD!)
- LocalAI lenTaking resources

Solución: Agregar GPU o aumentar CPU/RAM
```

### Si tu throughput es 0.6-1.0 pgs/sec
```
✅ Performance aceptable para:
- < 100 documentos/día (OK)
- < 1000 páginas/día (OK)

Recomendación: Monitorear. GPU ayudará cuando carga crezca.
```

### Si tu throughput es > 1.0 pgs/sec
```
🚀 Performance excelente! CPU muy poderoso.

Pero GPU seguirá siendo útil para:
- Picos de demanda
- Documentos muy grandes
- Múltiples usuarios simultáneos
```

---

## 🔄 Flujo de Testing Recomendado

### Fase 0: Baseline (HOY)
```bash
# 1. Captura performance actual CPU
bash scripts/run_benchmark.sh quick

# 2. Anota resultados:
# - Throughput actual: _____ pgs/sec
# - Tiempo/doc: _____ segundos

# 3. Proyecta con GPU: multiplica por 6
```

### Fase 1: Agregar GPU (Q2 2026)
```bash
# 1. Instala GPU drivers
# nvidia-smi  # Verifica GPU detectada

# 2. Reinicia servicios
docker compose restart idp_app

# 3. Re-test immediato
bash scripts/run_benchmark.sh quick

# 4. Compara:
# - Speedup actual vs proyectado
# - ¿Cercano a 6×?
```

### Fase 2: Agregar RunPod (Q3 2026)
```bash
# 1. Setup RunPod endpoint
# DOCLING_RUNPOD_ENABLED=true
# DOCLING_RUNPOD_ENDPOINT=...

# 2. Re-test
bash scripts/run_benchmark.sh standard

# 3. Mide throughput distribuido
```

---

## 📊 Benchmark Output Files

Todos los resultados se guardan en `benchmark_results/`:

```
benchmark_results/
├── benchmark_20260320_143022.csv    # CSV con métricas detalladas
├── benchmark_results.log             # Log completo de ejecución
└── test_5pages_1711000422.pdf       # PDFs de prueba generados
```

### CSV Columns:
```
test_name,document_name,pages,processing_device,processing_mode,time_elapsed,characters_output,cache_hit,throughput_pages_per_sec,memory_rss_mb,timestamp
```

**Uso del CSV:**
```bash
# Análisis simple con awk
tail -n +2 benchmark_20260320_143022.csv | awk -F, '{sum+=$6; n++} END {print "Avg time:", sum/n "s"}'

# O importar a Excel/Sheets para análisis
```

---

## 🛠️ Troubleshooting

### ❌ "pypdf not found"
```bash
pip install pypdf==4.0.1
```

### ❌ "vision_optimized module not found"
```bash
# Ejecutar desde directorio correcto
cd idp-smart

# O con PYTHONPATH explícita
PYTHONPATH=. python3 scripts/benchmark_docling.py --batch-test 1 5
```

### ❌ "Timeout en DocumentConverter"
```bash
# Docling puede tardar en inicializar modelos
# Primera ejecución: puede tardar hasta 30 segundos

# Solución: ejecutar con más tiempo
timeout 120 bash scripts/run_benchmark.sh quick
```

### ❌ "Memoria insuficiente"
```bash
# Si el sistema tiene < 8GB RAM
# Reducir test size:
bash scripts/run_benchmark.sh quick  # En vez de extended
```

---

## 📈 Performance Tuning Based on Results

### Si no tienes GPU y quieres mejorar CPU:

1. **Aumentar workers (parallelism)**
   ```bash
   VISION_PARALLEL_WORKERS=8  # vs 4 por defecto
   ```

2. **Usar cache agresivamente**
   ```bash
   VISION_USE_CACHE=true
   VISION_CACHE_TTL=2592000  # 30 days vs 7
   ```

3. **Reducir OCR quality en no-critical docs**
   ```bash
   VISION_OCR_QUALITY=fast  # vs standard
   ```

### Cuando agregues GPU:

1. **Auto se configura automáticamente** (no cambies nada)
2. **Monitor memoria:**
   ```bash
   docker exec idp_app nvidia-smi --query-gpu=memory.used,memory.free
   ```
3. **Si hay OOM, reduce batch o usa CPU fallback:**
   ```bash
   VISION_ALLOW_GPU=false
   ```

---

## 🎯 Métricas Clave a Monitorear

| Métrica | Objetivo | Acción si no cumple |
|---------|----------|-------------------|
| Throughput | > 0.6 pgs/s | Agregar más CPU cores |
| Consistency (StdDev) | < 20% media | Investigar ralentizaciones |
| Memory | < 4GB libre después | Aumentar RAM |
| Cache hit rate | > 80% (si docs repetidos) | Cache OK |

---

## 📞 Soporte & Siguientes Pasos

**Después de ejecutar benchmark:**

1. **Comparte resultados CSV** en issue o PR
2. **Compara con proyecciones** de GPU/RunPod
3. **Planifica next upgrade** basado en throughput actual

**Ejemplos para diferentes escenarios:**

- **10 docs/day:** CPU suficiente, monitor para crecimiento
- **50 docs/day:** GPU+ recomendado (Q2 2026)
- **100+ docs/day:** GPU + RunPod requerido (Q3 2026)

---

## 🚀 Próximo Paso

Después de correr benchmark:
1. Anota throughput actual
2. Lee [GPU_RUNPOD_OPTIMIZATION.md](../GPU_RUNPOD_OPTIMIZATION.md)
3. Planifica upgrade GPU cuando carga crezca

