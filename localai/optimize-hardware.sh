#!/bin/bash
# ============================================
# LocalAI Hardware Optimization Script
# Detecta configuración de hardware y optimiza parámetros
# ============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  LocalAI Hardware Optimization Script${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

# ============================================
# 1. DETECT CPU
# ============================================
echo -e "${YELLOW}[1/6] Detectando CPU...${NC}"

CPU_CORES=$(nproc)
CPU_MODEL=$(lscpu | grep "Model name" | sed 's/Model name:[[:space:]]*//')
CPU_THREADS=$((CPU_CORES * 2))  # Asumir 2 threads por core

echo -e "${GREEN}✓ CPU Detectado:${NC}"
echo "  - Modelo: $CPU_MODEL"
echo "  - Cores: $CPU_CORES"
echo "  - Threads: $CPU_THREADS"

# Detectar si tiene AVX-512 o AVX-2
if grep -q avx512 /proc/cpuinfo; then
    AVX512="SI"
    echo -e "  ${GREEN}✓ AVX-512: Soportado${NC}"
else
    AVX512="NO"
fi

if grep -q avx /proc/cpuinfo; then
    AVX2="SI"
    echo -e "  ${GREEN}✓ AVX-2: Soportado${NC}"
else
    AVX2="NO"
    echo -e "  ${RED}✗ AVX-2: NO Soportado (rendimiento limitado)${NC}"
fi

# ============================================
# 2. DETECT MEMORY
# ============================================
echo -e "\n${YELLOW}[2/6] Detectando Memoria...${NC}"

TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
TOTAL_MEM_GB=$((TOTAL_MEM_KB / 1024 / 1024))

echo -e "${GREEN}✓ RAM Total:${NC} ${TOTAL_MEM_GB}GB"

# Calcular límite de RAM para LocalAI
RECOMMENDED_LOCALAI_MEM=$((TOTAL_MEM_GB / 2))
if [ $RECOMMENDED_LOCALAI_MEM -lt 4 ]; then
    RECOMMENDED_LOCALAI_MEM=4
fi

# ============================================
# 3. DETECT GPU
# ============================================
echo -e "\n${YELLOW}[3/6] Detectando GPU...${NC}"

if command -v nvidia-smi &> /dev/null; then
    GPU_DETECTED="SI"
    GPU_COUNT=$(nvidia-smi -L | wc -l)
    GPU_MEMORY=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | awk '{print $1/1024}' | cut -d. -f1)
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
    
    echo -e "${GREEN}✓ GPU NVIDIA Detectada:${NC}"
    echo "  - GPU: $GPU_NAME"
    echo "  - Cantidad: $GPU_COUNT"
    echo "  - VRAM: ${GPU_MEMORY}GB"
    
    # Detectar generación
    if [[ "$GPU_NAME" =~ "A100" ]] || [[ "$GPU_NAME" =~ "H100" ]]; then
        GPU_GEN="HIGH_END"
    elif [[ "$GPU_NAME" =~ "4090" ]] || [[ "$GPU_NAME" =~ "4080" ]]; then
        GPU_GEN="HIGH_END"
    elif [[ "$GPU_NAME" =~ "3090" ]] || [[ "$GPU_NAME" =~ "3080" ]]; then
        GPU_GEN="MID_HIGH"
    elif [[ "$GPU_NAME" =~ "3060" ]] || [[ "$GPU_NAME" =~ "2080" ]]; then
        GPU_GEN="MID"
    else
        GPU_GEN="LOW"
    fi
else
    GPU_DETECTED="NO"
    echo -e "${YELLOW}⚠ No GPU NVIDIA detectada${NC}"
fi

# ============================================
# 4. DETECT DISK SPACE
# ============================================
echo -e "\n${YELLOW}[4/6] Detectando Espacio en Disco...${NC}"

DISK_AVAIL=$(df -BG "$PROJECT_ROOT" | tail -1 | awk '{print $4}' | sed 's/G//')
echo -e "${GREEN}✓ Espacio Disponible:${NC} ${DISK_AVAIL}GB"

if [ "$DISK_AVAIL" -lt 10 ]; then
    echo -e "${RED}✗ ADVERTENCIA: Se requieren mínimo 10GB para modelos${NC}"
fi

# ============================================
# 5. RECOMMEND CONFIGURATION
# ============================================
echo -e "\n${YELLOW}[5/6] Generando Recomendaciones...${NC}\n"

# Determinar configuración óptima
THREADS=$((CPU_CORES / 2))
if [ $THREADS -lt 2 ]; then
    THREADS=2
fi

if [ "$GPU_DETECTED" = "SI" ]; then
    RECOMMENDED_CONFIG="GPU_ACCELERATION"
    BACKEND="CUDA"
    
    # Calcular GPU layers
    if [ "$GPU_GEN" = "HIGH_END" ]; then
        GPU_LAYERS=50
    elif [ "$GPU_GEN" = "MID_HIGH" ]; then
        GPU_LAYERS=35
    else
        GPU_LAYERS=20
    fi
    
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}🚀 CONFIGURACIÓN RECOMENDADA: GPU ACCELERATION${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "Backend: CUDA (NVIDIA GPU)"
    echo "Threads: $THREADS"
    echo "GPU Layers: $GPU_LAYERS"
    echo ""
    echo "Esperado:"
    echo "  - Tokens/seg: 60-80"
    echo "  - Documentos/min: 12-15"
    echo "  - Tiempo para 100 formularios: ~2-3 minutos"
    echo ""
    
elif [ "$AVX512" = "SI" ]; then
    RECOMMENDED_CONFIG="OPENVINO_AVX512"
    BACKEND="OpenVINO"
    
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}⚡ CONFIGURACIÓN RECOMENDADA: OpenVINO AVX-512${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "Backend: OpenVINO"
    echo "Threads: $THREADS"
    echo "SIMD: AVX-512"
    echo ""
    echo "Esperado:"
    echo "  - Tokens/seg: 25-35"
    echo "  - Documentos/min: 6-8"
    echo "  - Tiempo para 100 formularios: ~4-5 minutos"
    echo ""
    
else
    RECOMMENDED_CONFIG="CPU_AVX2"
    BACKEND="CPU"
    
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}💻 CONFIGURACIÓN RECOMENDADA: CPU (AVX-2)${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "Backend: CPU (AVX-2)"
    echo "Threads: $THREADS"
    echo ""
    echo "Esperado:"
    echo "  - Tokens/seg: 15-20"
    echo "  - Documentos/min: 3-5"
    echo "  - Tiempo para 100 formularios: ~20-30 minutos"
    echo ""
fi

# ============================================
# 6. GENERATE CONFIGURATION
# ============================================
echo -e "${YELLOW}[6/6] Generando archivos de configuración...${NC}\n"

# Crear directorio si no existe
mkdir -p "$PROJECT_ROOT/localai/config"

# Actualizar docker-compose override
case $RECOMMENDED_CONFIG in
    GPU_ACCELERATION)
        cat > "$PROJECT_ROOT/docker-compose.override.yml" << 'COMPOSE_EOF'
version: '3.8'

services:
  localai:
    image: localai/localai:latest-aio-cuda-12
    environment:
      - THREADS=4
      - GPU_LAYERS=35
      - CUDA_VISIBLE_DEVICES=0
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
COMPOSE_EOF
        echo -e "${GREEN}✓ Generado: docker-compose.override.yml${NC}"
        ;;
    OPENVINO_AVX512)
        cat > "$PROJECT_ROOT/docker-compose.override.yml" << 'COMPOSE_EOF'
version: '3.8'

services:
  localai:
    image: localai/localai:latest-aio-cpu-avx2
    environment:
      - THREADS=8
      - INTEL_OPENVINO_ENABLED=1
      - OPENVINO_DEVICE=CPU
      - OPENVINO_NUM_INFER_REQUESTS=4
COMPOSE_EOF
        echo -e "${GREEN}✓ Generado: docker-compose.override.yml${NC}"
        ;;
    *)
        cat > "$PROJECT_ROOT/docker-compose.override.yml" << 'COMPOSE_EOF'
version: '3.8'

services:
  localai:
    image: localai/localai:latest-aio-cpu-avx2
    environment:
      - THREADS=4
      - CUDA_VISIBLE_DEVICES="-1"
COMPOSE_EOF
        echo -e "${GREEN}✓ Generado: docker-compose.override.yml${NC}"
        ;;
esac

# Generar .env.optimized
cat > "$PROJECT_ROOT/.env.optimized" << EOF
# Auto-generated configuration
# Generated: $(date)
# Hardware: $CPU_CORES cores, ${TOTAL_MEM_GB}GB RAM, GPU=$GPU_DETECTED

LLM_PROVIDER=localai
LOCALAI_BASE_URL=http://localai:8080/v1
LOCALAI_MODEL=granite-vision
LOCALAI_TEMPERATURE=0.1
LOCALAI_CONTEXT_SIZE=8192
LOCALAI_MAX_TOKENS=2048
LOCALAI_TIMEOUT=300

# Hardware Configuration
THREADS=$THREADS
DEBUG=0
LOG_LEVEL=INFO

EOF

echo -e "${GREEN}✓ Generado: .env.optimized${NC}"

# ============================================
# SUMMARY
# ============================================
echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}📊 RESUMEN DE OPTIMIZACIÓN${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

echo "Configuración Detectada:"
echo "  CPU: $CPU_CORES cores ($CPU_MODEL)"
echo "  RAM: ${TOTAL_MEM_GB}GB"
echo "  GPU: $GPU_DETECTED ($GPU_NAME 2>/dev/null || echo '(N/A)')"
echo "  Disco: ${DISK_AVAIL}GB disponibles"
echo ""
echo "Recomendación: $RECOMMENDED_CONFIG"
echo "Backend: $BACKEND"
echo ""
echo "Próximos pasos:"
echo "  1. Revisar docker-compose.override.yml"
echo "  2. Copiar .env.optimized a .env: cp .env.optimized .env"
echo "  3. Iniciar servicios: docker compose up -d"
echo "  4. Monitorear: docker logs -f idp_localai"
echo ""
echo -e "${GREEN}✓ Optimización completada${NC}\n"

