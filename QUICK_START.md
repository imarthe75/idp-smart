# ✅ Plan: Ejecutar Todo Localmente con LocalAI

## 🎯 Estado Actual (Marzo 2026)

```
Backend: ✅ Actualizado a LocalAI (antes era Google Gemini)
.env: ✅ Cambiado a LLM_PROVIDER=localai
Docker Compose: ✅ LocalAI incluido (puerto 8080)
Code: ✅ Soporta Docling + Granite + LocalAI
```

---

## 🚀 PASOS PARA EJECUTAR HOY

### **Paso 1: Preparar Ambiente (5 minutos)**

```bash
cd idp-smart

# Verificar .env está actualizado
grep "LLM_PROVIDER" .env
# Debe mostrar: LLM_PROVIDER=localai ✅

# Instalar dependencias Python (si no está activado venv)
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

### **Paso 2: Auto-detectar Hardware (3 minutos)**

```bash
# Este script detecta si tienes GPU NVIDIA y configura todo
bash localai/optimize-hardware.sh

# Responderá algo como:
# "✓ GPU NVIDIA Detectada: RTX 4090"  ← Si tienes GPU
# o
# "✓ CPU Intel Detectado: i7-12700K"  ← Si solo tienes CPU

# Se genera archivo: docker-compose.override.yml
```

### **Paso 3: Iniciar Docker (2 minutos)**

```bash
# Limpiar si hay containers viejos
docker compose down

# Construir + iniciar todo
docker compose up -d

# Ver que todo está corriendo
docker compose ps
# Debe mostrar 7 servicios ✅
```

### **Paso 4: Esperar a LocalAI (2-5 minutos)**

```bash
# Ver cuando cargó el modelo Granite
docker logs -f idp_localai | grep -i "loaded\|ready"

# Esperar a ver línea como:
# "Model loaded successfully"
# o
# "ready for predictions"

# Una vez veas eso, presionar Ctrl+C para salir del log
```

### **Paso 5: Verificar Todo Funciona (2 minutos)**

```bash
# Ejecutar script de test
bash scripts/test-localai-setup.sh

# Debe mostrar ✅ en todos los tests
```

### **Paso 6: Abrir UI (1 segundo)**

```
http://localhost:5173
```

```
O ver API docs:
http://localhost:8000/docs
```

---

## 🎯 Script Rápido (Todo en 1 comando)

Si quieres total automatización:

```bash
#!/bin/bash
cd idp-smart

# Auto-setup
bash localai/optimize-hardware.sh
docker compose down
docker compose up -d

# Esperar a LocalAI
echo "⏳ Esperando a LocalAI (2-5 minutos)..."
while ! curl -s http://localhost:8080/v1/models &>/dev/null; do
    sleep 5
    echo "   ...todavía descargando modelo"
done

echo "✅ Listo!"
bash scripts/test-localai-setup.sh
```

Guardar como `start-local.sh` y ejecutar:
```bash
bash start-local.sh
```

---

## ⏱️ Tiempo Total

- Paso 1: 5 min
- Paso 2: 3 min
- Paso 3: 2 min
- Paso 4: 2-5 min ⏳ **ESPERAR**
- Paso 5: 2 min
- Paso 6: 1 seg
- **TOTAL: ~15-20 minutos**

---

## 📊 Después de Levantar: Próximos Pasos

### **A. Probar con Documento Real**

1. Abrir http://localhost:5173
2. Seleccionar acto: **BI34** (Patrimonio Familiar)
3. Subir PDF test
4. Ver progress en real-time en UI

### **B. Monitorear Logs (Abrir nuevas terminales)**

```bash
# Terminal 2: Ver Celery procesando
docker logs -f idp_worker

# Terminal 3: Ver LocalAI infiriendo
docker logs -f idp_localai

# Terminal 4: Ver API
docker logs -f idp_api
```

### **C. Benchmarking Local**

Procesar 5 documentos y medir tiempos:

```bash
# En cada acción anotar los tiempos en cada etapa
# y comparar con tabla de esperados:

| Hardware | Docling | LocalAI | Total |
|----------|---------|---------|-------|
| CPU i7   | 2 min   | 1 min   | 3 min |
| RTX 3090 | 0.5 min | 0.3 min | 0.8 min |
| RTX 4090 | 0.3 min | 0.2 min | 0.5 min |
```

---

## 🐛 Troubleshooting Rápido

### "Connection refused - LocalAI"
```bash
# LocalAI aún descargando modelo
docker logs idp_localai | tail -5
# Esperar más, es normal (2-5 min primera vez)
```

### "Out of memory"
```bash
# Reducir threads
echo "THREADS=2" >> .env

# O reducir context size
# Editar localai/config/granite-vision.yaml:
#   context_size: 4096  (en lugar de 8192)

docker compose restart localai
```

### "Model not found"
```bash
# Verificar que se descargó
ls -lh localai/models/
# Debe mostrar: granite-2b-vision-q4cm.gguf (~730MB)

# Si no existe, triggear descarga
docker exec idp_localai curl -X POST http://localhost:8080/v1/models/load
```

---

## 📚 Documentación Completa

Si necesitas detalles:

- [ARQUITECTURA_LOCALAI.md](ARQUITECTURA_LOCALAI.md) - Cómo funciona cada componente
- [LOCAL_EXECUTION_GUIDE.md](LOCAL_EXECUTION_GUIDE.md) - Guía completa de ejecución
- [RUNPOD_INTEGRATION.md](RUNPOD_INTEGRATION.md) - Opcional: Usar GPU en RunPod

---

## ✨ Bonus: Versión Producción (Después)

Una vez todo funciona local, para producción tienes opciones:

1. **Mantener local** - Si tienes GPU buena
2. **Usar RunPod** - Si quieres serverless/escalable
3. **Híbrido** - Docling local + LocalAI en RunPod

Ver [RUNPOD_INTEGRATION.md](RUNPOD_INTEGRATION.md) para detalles.

---

## ✅ Checklist Final

- [ ] `.env` tiene `LLM_PROVIDER=localai`
- [ ] `docker compose ps` muestra 7 servicios
- [ ] LocalAI cargó el modelo (docker logs)
- [ ] `bash scripts/test-localai-setup.sh` pasa todos los tests
- [ ] UI abre en http://localhost:5173
- [ ] Puedo subir documentos y procesar

¡Listo! 🎉

