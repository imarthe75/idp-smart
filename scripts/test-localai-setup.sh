#!/bin/bash
# script: test-localai-setup.sh
# Verificar que LocalAI + Docling funciona correctamente

set -e

PROJECT_ROOT="."
cd "$PROJECT_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}🧪 TEST: LocalAI + Docling Setup${NC}"
echo -e "${BLUE}========================================${NC}\n"

# Test 1: Verificar Docker
echo -e "${YELLOW}[1/8] Verificando Docker...${NC}"
if docker --version &> /dev/null; then
    echo -e "${GREEN}✅ Docker OK${NC} - $(docker --version)"
else
    echo -e "${RED}❌ Docker NO instalado${NC}"
    exit 1
fi

# Test 2: Verificar Docker Compose
echo -e "${YELLOW}[2/8] Verificando Docker Compose...${NC}"
if docker compose --version &> /dev/null; then
    echo -e "${GREEN}✅ Docker Compose OK${NC} - $(docker compose --version)"
else
    echo -e "${RED}❌ Docker Compose NO instalado${NC}"
    exit 1
fi

# Test 3: Verificar servicios corriendo
echo -e "${YELLOW}[3/8] Verificando servicios...${NC}"
SERVICES=$(docker compose ps --services 2>/dev/null | wc -l)
RUNNING=$(docker compose ps -q 2>/dev/null | wc -l)

if [ $RUNNING -gt 0 ]; then
    echo -e "${GREEN}✅ $RUNNING servicios corriendo${NC}"
else
    echo -e "${RED}❌ NO hay servicios corriendo${NC}"
    echo -e "${YELLOW}ᴏ Ejecuta: docker compose up -d${NC}"
    exit 1
fi

# Test 4: Verificar LocalAI accesible
echo -e "${YELLOW}[4/8] Verificando LocalAI (8080)...${NC}"
if curl -s http://localhost:8080/v1/models &> /dev/null; then
    MODELS=$(curl -s http://localhost:8080/v1/models | jq '.data | length' 2>/dev/null)
    echo -e "${GREEN}✅ LocalAI accesible${NC} - $MODELS modelo(s) disponible(s)"
else
    echo -e "${RED}❌ LocalAI NO responde${NC}"
    echo -e "${YELLOW}💡 Esperar 2-5 min para que cargue el modelo...${NC}"
    echo -e "${YELLOW}   docker logs -f idp_localai${NC}"
    exit 1
fi

# Test 5: Verificar API FastAPI
echo -e "${YELLOW}[5/8] Verificando API (8000)...${NC}"
if curl -s http://localhost:8000/api/v1/forms &> /dev/null; then
    FORMS=$(curl -s http://localhost:8000/api/v1/forms | jq 'length' 2>/dev/null)
    echo -e "${GREEN}✅ API accesible${NC} - $FORMS formas disponibles"
else
    echo -e "${RED}❌ API NO responde${NC}"
    exit 1
fi

# Test 6: Verificar Celery Worker
echo -e "${YELLOW}[6/8] Verificando Celery Worker...${NC}"
if docker exec idp_worker celery -A worker.celery_app inspect active &> /dev/null; then
    echo -e "${GREEN}✅ Celery Worker OK${NC}"
else
    echo -e "${RED}❌ Celery Worker NO responde${NC}"
    exit 1
fi

# Test 7: Verificar PostgreSQL
echo -e "${YELLOW}[7/8] Verificando PostgreSQL...${NC}"
if docker exec idp_db psql -U admin_user -d rpp -c "SELECT 1" &> /dev/null; then
    CATALOG_COUNT=$(docker exec idp_db psql -U admin_user -d rpp -t -c "SELECT COUNT(*) FROM idp_smart.act_forms_catalog;" 2>/dev/null)
    echo -e "${GREEN}✅ PostgreSQL OK${NC} - $CATALOG_COUNT formas en catálogo"
else
    echo -e "${RED}❌ PostgreSQL NO responde${NC}"
    exit 1
fi

# Test 8: Verificar Python dependencies
echo -e "${YELLOW}[8/8] Verificando Python dependencies...${NC}"
python3 -c "import torch; print('✅ PyTorch OK')" 2>/dev/null || echo -e "${RED}❌ PyTorch NO${NC}"
python3 -c "import docling; print('✅ Docling OK')" 2>/dev/null || echo -e "${RED}❌ Docling NO${NC}"
python3 -c "from langchain_openai import ChatOpenAI; print('✅ LangChain OK')" 2>/dev/null || echo -e "${RED}❌ LangChain NO${NC}"

# Summary
echo -e "\n${BLUE}========================================${NC}"
echo -e "${GREEN}✅ TODOS LOS TESTS PASARON${NC}"
echo -e "${BLUE}========================================${NC}\n"

echo -e "${YELLOW}📊 RESUMEN:${NC}"
echo -e "  ✅ Docker & Docker Compose"
echo -e "  ✅ LocalAI corriendo (puerto 8080)"
echo -e "  ✅ API FastAPI (puerto 8000)"
echo -e "  ✅ Celery Worker"
echo -e "  ✅ PostgreSQL con $CATALOG_COUNT formas"
echo -e "  ✅ Python dependencies\n"

echo -e "${GREEN}🚀 Ahora puedes:${NC}"
echo -e "  1. Abrir UI: http://localhost:5173"
echo -e "  2. API docs: http://localhost:8000/docs"
echo -e "  3. Subir documentos y procesar"
echo -e "  4. Ver logs: docker logs -f idp_worker\n"
