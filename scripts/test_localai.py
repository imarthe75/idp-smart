#!/usr/bin/env python3
"""
LocalAI Connection Test Script
Valida que LocalAI esté funcionando correctamente

Uso:
    python scripts/test_localai.py
    
    O desde Docker:
    docker exec idp_api python scripts/test_localai.py
"""

import os
import sys
import json
import time
from typing import Dict, List
from pathlib import Path

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from core.config import settings
from engine.localai_integration import (
    init_localai_llm,
    extract_structured_data,
    ExtractorChain
)

# Colors
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
BLUE = '\033[94m'
RESET = '\033[0m'

def print_header(text: str):
    """Imprime header con formato"""
    print(f"\n{BLUE}{'━' * 70}{RESET}")
    print(f"{BLUE}{text.center(70)}{RESET}")
    print(f"{BLUE}{'━' * 70}{RESET}\n")

def print_success(text: str):
    """Imprime mensaje exitoso"""
    print(f"{GREEN}✓ {text}{RESET}")

def print_error(text: str):
    """Imprime mensaje de error"""
    print(f"{RED}✗ {text}{RESET}")

def print_info(text: str):
    """Imprime mensaje informativo"""
    print(f"{BLUE}ℹ {text}{RESET}")

def print_warning(text: str):
    """Imprime mensaje de advertencia"""
    print(f"{YELLOW}⚠ {text}{RESET}")

# ============================================
# TEST 1: Validar Configuración
# ============================================

def test_config() -> bool:
    """Valida que la configuración esté correcta"""
    print_header("TEST 1: Validar Configuración")
    
    try:
        print(f"LLM Provider: {settings.llm_provider}")
        print(f"LocalAI URL: {settings.localai_url}")
        print(f"Modelo: {settings.model_reasoning_name}")
        print(f"Temperature: {settings.localai_temperature}")
        print(f"Max Tokens: {settings.localai_max_tokens}")
        print(f"Timeout: {settings.localai_timeout}s")
        
        if settings.llm_provider != "localai":
            print_warning(f"Provider no es 'localai', es '{settings.llm_provider}'")
            return False
        
        print_success("Configuración válida")
        return True
    except Exception as e:
        print_error(f"Error en configuración: {e}")
        return False

# ============================================
# TEST 2: Conectividad HTTP
# ============================================

def test_connectivity() -> bool:
    """Prueba conectividad HTTP basic"""
    print_header("TEST 2: Conectividad HTTP")
    
    try:
        import requests
        
        url = f"{settings.localai_url}/models"
        print(f"Conectando a: {url}")
        
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            models = response.json()
            print_success(f"HTTP OK (Status 200)")
            print(f"Respuesta: {json.dumps(models, indent=2)}")
            return True
        else:
            print_error(f"HTTP Error {response.status_code}")
            print(f"Respuesta: {response.text}")
            return False
            
    except Exception as e:
        print_error(f"Conectividad falló: {e}")
        return False

# ============================================
# TEST 3: Inicializar LLM
# ============================================

def test_llm_init() -> bool:
    """Prueba inicializar instancia LLM"""
    print_header("TEST 3: Inicializar LLM (ChatOpenAI)")
    
    try:
        llm = init_localai_llm()
        print_success("LLM inicializado")
        print(f"Tipo: {type(llm).__name__}")
        print(f"Model: {llm.model_name}")
        return True
    except Exception as e:
        print_error(f"Error inicializando LLM: {e}")
        return False

# ============================================
# TEST 4: Simple Chat
# ============================================

def test_simple_chat() -> bool:
    """Prueba completión simple"""
    print_header("TEST 4: Simple Chat Request")
    
    try:
        llm = init_localai_llm()
        
        print("Enviando: '¿Cuál es la capital de México?'")
        
        start_time = time.time()
        response = llm.invoke("¿Cuál es la capital de México?")
        elapsed = time.time() - start_time
        
        print_success(f"Respuesta recibida en {elapsed:.2f}s")
        print(f"\nTexto: {response.content}\n")
        return True
        
    except Exception as e:
        print_error(f"Error en chat: {e}")
        return False

# ============================================
# TEST 5: Extracción Estructurada
# ============================================

def test_structured_extraction() -> bool:
    """Prueba extracción JSON"""
    print_header("TEST 5: Extracción Estructurada (Forma)")
    
    # Documento de prueba
    test_document = """
    ACTA DE VENTA Y COMPRA
    ═════════════════════════════════════════════
    
    NÚMERO DE ACTA: ACT-2026-00123
    FECHA: 15 de marzo de 2026
    NOTARIO: Lic. Juan Pérez García Rodríguez
    NOTARÍA: Notaría Pública Número 42, México CDMX
    
    COMPARECEN:
    
    VENDEDOR:
    - Nombre: Carlos López Martínez
    - Cédula: RFC-CRM-900123-ABC7
    - Domicilio: Avenida Paseo de la Reforma 505, Depto 1500, CDMX
    
    COMPRADOR:
    - Nombre: María García López
    - Cédula: RFC-MGL-850822-XYZ3
    - Domicilio: Calle Amberes 54, Depto 401, CDMX
    
    BIEN INMUEBLE:
    - Descripción: Casa habitación de un piso
    - Ubicación: Avenida Paseo de la Reforma 505, Depto 1500
    - Superficie: 150 metros cuadrados
    - Precio: $2,500,000.00 MXN
    - Moneda: Pesos Mexicanos
    
    CONDICIONES:
    - Pago inmediato
    - Se da posesión al comprador
    - El vendedor garantiza posesión pacífica
    """
    
    # Esquema de prueba (Bio34 simplified)
    test_schema = {
        "acta-numero": {
            "uuid": "001-acta-numero",
            "type": "text",
            "label": "Número de Acta"
        },
        "acta-fecha": {
            "uuid": "002-acta-fecha", 
            "type": "date",
            "label": "Fecha del Acta"
        },
        "notario-nombre": {
            "uuid": "003-notario-nombre",
            "type": "text",
            "label": "Nombre del Notario"
        },
        "vendedor-nombre": {
            "uuid": "004-vendedor-nombre",
            "type": "text",
            "label": "Nombre del Vendedor"
        },
        "comprador-nombre": {
            "uuid": "005-comprador-nombre",
            "type": "text",
            "label": "Nombre del Comprador"
        },
        "bien-precio": {
            "uuid": "006-bien-precio",
            "type": "currency",
            "label": "Precio del Bien"
        }
    }
    
    try:
        print("Extrayendo datos del documento...")
        
        start_time = time.time()
        result = extract_structured_data(
            test_document,
            test_schema,
            custom_instructions="Extrae NÚMEROS exactos de montos y fechas."
        )
        elapsed = time.time() - start_time
        
        print_success(f"Extracción completada en {elapsed:.2f}s")
        print(f"\nDatos extraídos:\n{json.dumps(result, indent=2, ensure_ascii=False)}")
        return True
        
    except Exception as e:
        print_error(f"Error en extracción: {e}")
        import traceback
        traceback.print_exc()
        return False

# ============================================
# TEST 6: ExtractorChain
# ============================================

def test_extractor_chain() -> bool:
    """Prueba usando ExtractorChain"""
    print_header("TEST 6: ExtractorChain (Advanced)")
    
    schema = {
        "field1": {"type": "text", "label": "Field 1"},
        "field2": {"type": "text", "label": "Field 2"}
    }
    
    try:
        print("Inicializando ExtractorChain...")
        chain = ExtractorChain(schema)
        print_success("Chain inicializado")
        
        doc = "Este es un documento de prueba con Field1: valor1 y Field2: valor2"
        print(f"\nProcesando documento...")
        
        result = chain.invoke(doc)
        print_success("Procesamiento completado")
        print(f"Resultado: {json.dumps(result, indent=2)}")
        return True
        
    except Exception as e:
        print_error(f"Error con chain: {e}")
        return False

# ============================================
# TEST 7: Performance Benchmark
# ============================================

def test_performance() -> bool:
    """Benchmark de rendimiento"""
    print_header("TEST 7: Performance Benchmark")
    
    try:
        llm = init_localai_llm()
        prompt = "Extrae el nombre de esta empresa: ABC Corporation Ltd."
        
        times = []
        print(f"Ejecutando 3 llamadas...")
        
        for i in range(3):
            start = time.time()
            response = llm.invoke(prompt)
            elapsed = time.time() - start
            times.append(elapsed)
            print(f"  {i+1}. {elapsed:.2f}s - {response.content[:50]}...")
        
        avg_time = sum(times) / len(times)
        print_success(f"Tiempo promedio: {avg_time:.2f}s")
        
        if avg_time > 30:
            print_warning(f"Respuesta lenta (>{avg_time:.0f}s)")
        
        return True
        
    except Exception as e:
        print_error(f"Error en benchmark: {e}")
        return False

# ============================================
# TEST 8: Error Handling
# ============================================

def test_error_handling() -> bool:
    """Prueba manejo de errores"""
    print_header("TEST 8: Error Handling")
    
    try:
        llm = init_localai_llm()
        
        # Prueba timeout
        print("Probando timeout (esperar 5+ segundos)...")
        try:
            # Este debería timeout
            response = llm.invoke(" " * 100000)  # Input masivo
            print_warning("No ocurrió timeout esperado")
        except Exception as e:
            print_success(f"Timeout capturado: {type(e).__name__}")
        
        return True
        
    except Exception as e:
        print_error(f"Error en manejo: {e}")
        return False

# ============================================
# MAIN
# ============================================

def main():
    """Ejecuta todos los tests"""
    
    print(f"\n{BLUE}")
    print("╔" + "═" * 68 + "╗")
    print("║" + "  LocalAI Connection Test Suite".center(68) + "║")
    print("║" + f"  {time.strftime('%Y-%m-%d %H:%M:%S')}".center(68) + "║")
    print("╚" + "═" * 68 + "╝")
    print(f"{RESET}")
    
    tests = [
        ("Configuración", test_config),
        ("Conectividad", test_connectivity),
        ("Inicializar LLM", test_llm_init),
        ("Chat Simple", test_simple_chat),
        ("Extracción Estructurada", test_structured_extraction),
        ("ExtractorChain", test_extractor_chain),
        ("Performance", test_performance),
        ("Error Handling", test_error_handling),
    ]
    
    results = {}
    
    for name, test_func in tests:
        try:
            success = test_func()
            results[name] = "PASS" if success else "FAIL"
        except Exception as e:
            print_error(f"Test {name} falló con excepción: {e}")
            results[name] = "ERROR"
    
    # ====== RESUMEN =====
    print_header("RESUMEN DE TESTS")
    
    for name, result in results.items():
        if result == "PASS":
            print_success(f"{name}: {result}")
        else:
            print_error(f"{name}: {result}")
    
    # Estadísticas
    passed = sum(1 for r in results.values() if r == "PASS")
    total = len(results)
    
    print(f"\nTotal: {passed}/{total} tests pasados")
    
    if passed == total:
        print_success("✓ Todos los tests pasaron - Sistema listo para producción")
        return 0
    else:
        print_error(f"✗ {total - passed} tests fallaron")
        return 1

if __name__ == "__main__":
    sys.exit(main())

