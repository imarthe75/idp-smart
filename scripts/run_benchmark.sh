#!/bin/bash
# 🚀 Benchmark Runner - Docling Vision Engine Performance Testing
# ⚠️  IMPORTANTE: Este script ejecuta benchmarking DENTRO del Docker container
#    donde están todas las dependencias (docling, pypdf, torch, etc.)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BENCHMARK_SCRIPT="/app/benchmark_docling.py"  # Ruta dentro del container
CONTAINER_NAME="idp_api"  # Nombre del container Docker

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Función para imprimir con color
log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

log_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

log_warn() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

log_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Función de help
show_help() {
    cat << EOF
🚀 Docling Benchmarking Script (dentro de Docker)

USAGE:
    $0 [COMMAND] [OPTIONS]

COMMANDS:
    quick           Run quick test (1 document, 5 pages) - ~1 min
    standard        Run standard test (3 documents, 5 pages each) - ~3 min
    extended        Run extended test (10 documents, various sizes) - ~15 min
    cache           Test cache effectiveness
    full            Run all tests + cache
    custom          Custom test (specific file)

EXAMPLES:
    # Quick benchmark
    $0 quick

    # Standard benchmark
    $0 standard

    # All tests
    $0 full

NOTA IMPORTANTE:
    - El benchmarking corre DENTRO del Docker container idp_api
    - Se necesita: docker compose up -d (servicios ejecutándose)
    - Resultados se guardan en: ./benchmark_results/ (en el HOST)

EOF
}

# Verificar que Docker está disponible
if ! command -v docker &> /dev/null; then
    log_error "Docker no encontrado - requiere docker para ejecutar benchmarks"
    exit 1
fi

# Verificar que el container está corriendo
check_docker_container() {
    log_info "Verificando Docker container: $CONTAINER_NAME..."
    
    if ! docker ps --filter "name=$CONTAINER_NAME" --filter "status=running" | grep -q "$CONTAINER_NAME"; then
        log_error "Container $CONTAINER_NAME NO está ejecutándose"
        log_info "Inicia servicios con: docker compose up -d"
        exit 1
    fi
    
    log_success "Container $CONTAINER_NAME está corriendo"
}

# Ejecutar tests
run_benchmark() {
    local cmd="$1"
    local output_dir="benchmark_results"
    
    log_info "Iniciando benchmark (dentro de Docker): $cmd"
    
    case "$cmd" in
        quick)
            log_info "Quick test: 1 documento, 5 páginas (~1 minuto)"
            docker exec "$CONTAINER_NAME" python3 "$BENCHMARK_SCRIPT" \
                --batch-test 1 5 \
                --output-dir "$output_dir"
            ;;
        
        standard)
            log_info "Standard test: 3 documentos, 5 páginas (~3 minutos)"
            docker exec "$CONTAINER_NAME" python3 "$BENCHMARK_SCRIPT" \
                --batch-test 3 5 \
                --output-dir "$output_dir"
            ;;
        
        extended)
            log_info "Extended test: documentos variados (~15 minutos)"
            docker exec "$CONTAINER_NAME" python3 "$BENCHMARK_SCRIPT" \
                --batch-test 5 5 \
                --batch-test 3 10 \
                --batch-test 2 20 \
                --output-dir "$output_dir"
            ;;
        
        cache)
            log_info "Cache effectiveness test"
            docker exec "$CONTAINER_NAME" python3 "$BENCHMARK_SCRIPT" \
                --cache-test \
                --output-dir "$output_dir"
            ;;
        
        full)
            log_info "Full benchmark: todos los tests"
            docker exec "$CONTAINER_NAME" python3 "$BENCHMARK_SCRIPT" \
                --all \
                --cache-test \
                --output-dir "$output_dir"
            ;;
        
        custom)
            if [ -z "$2" ]; then
                log_error "Debes proporcionar archivo PDF"
                show_help
                exit 1
            fi
            log_info "Testing: $2"
            docker exec "$CONTAINER_NAME" python3 "$BENCHMARK_SCRIPT" \
                --test-file "$2" \
                --output-dir "$output_dir"
            ;;
        
        *)
            log_error "Comando desconocido: $cmd"
            show_help
            exit 1
            ;;
    esac
    
    # Copiar resultados del container al host
    log_info "Copiando resultados del container al host..."
    docker cp "$CONTAINER_NAME:/app/$output_dir" ./
    
    # Mostrar resultados
    log_success "Benchmark completado!"
    log_info "Resultados guardados en: $(pwd)/$output_dir"
}

# Main
if [ $# -eq 0 ]; then
    show_help
    exit 0
fi

log_info "📊 Docling Vision Engine Benchmarking (Docker)"
log_info "=============================================="

check_docker_container

run_benchmark "$@"

log_success "✅ Benchmark completado!"
log_info "Revisa resultados en: ./benchmark_results"

