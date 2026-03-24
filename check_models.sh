#!/bin/bash
# Script para monitorear estado de carga de modelos

echo "📊 Verificando modelos cargados en LocalAI..."
echo "=============================================="

while true; do
    clear
    echo "📊 Estado de Modelos en LocalAI"
    echo "=============================================="
    echo ""
    
    # Verificar modelos
    MODELS=$(curl -s http://localhost:8080/v1/models)
    MODEL_COUNT=$(echo "$MODELS" | grep -o '"id"' | wc -l)
    
    if [ $MODEL_COUNT -eq 0 ]; then
        echo "⏳ Descargando modelos..."
        echo "   (Puede tomar 10-30 minutos)"
        echo ""
        docker logs idp_localai 2>&1 | grep -i "download\|loading\|loaded" | tail -5
    else
        echo "✅ Modelos Disponibles: $MODEL_COUNT"
        echo ""
        echo "$MODELS" | grep -o '"id":"[^"]*"' | cut -d'"' -f4
    fi
    
    echo ""
    echo "Presiona Ctrl+C para salir"
    echo "Verificando cada 30 segundos..."
    sleep 30
done
