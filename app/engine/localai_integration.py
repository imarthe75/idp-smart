"""
LocalAI Integration Examples for LangChain
==========================================

Este módulo proporciona ejemplos de integración de LocalAI con LangChain
para procesar múltiples esquemas dinámicos obtenidos de act_forms_catalog.

Soporta tipos de actos mexicanos variados:
- BI1, BI34, BI58, BI32, BI6, BI26, BI4, BI16, BI49, etc.

Pipeline:
1. Docling: Extrae texto y estructura del documento (PDF/IMG)
2. Granite Vision: Análisis multimodal del contenido
3. LocalAI: Extracción estructurada según esquema dinámico de act_forms_catalog

Última actualización: Marzo 2026
"""

from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
import os
import json
from typing import Dict, List, Optional

# ============================================
# CONFIGURACIÓN BÁSICA
# ============================================

# Opción 1: Desde variables de entorno
from dotenv import load_dotenv

load_dotenv()

LOCALAI_BASE_URL = os.getenv("LOCALAI_BASE_URL", "http://localhost:8080/v1")
LOCALAI_MODEL = os.getenv("LOCALAI_MODEL", "granite-vision")
LOCALAI_TEMPERATURE = float(os.getenv("LOCALAI_TEMPERATURE", "0.1"))
LOCALAI_MAX_TOKENS = int(os.getenv("LOCALAI_MAX_TOKENS", "2048"))
LOCALAI_TIMEOUT = int(os.getenv("LOCALAI_TIMEOUT", "300"))

# ============================================
# INICIALIZAR LLM
# ============================================

def init_localai_llm() -> ChatOpenAI:
    """
    Inicializa conexión a LocalAI con valores por defecto optimizados.
    
    Returns:
        ChatOpenAI: Instancia configurada de LLM compatible con OpenAI
        
    Example:
        >>> llm = init_localai_llm()
        >>> response = llm.invoke("Hola, ¿cómo estás?")
        >>> print(response.content)
    """
    return ChatOpenAI(
        base_url=LOCALAI_BASE_URL,
        api_key="not-needed",  # LocalAI no requiere API key
        model=LOCALAI_MODEL,
        temperature=LOCALAI_TEMPERATURE,
        max_tokens=LOCALAI_MAX_TOKENS,
        timeout=LOCALAI_TIMEOUT,
        verbose=True
    )


# ============================================
# EXTRACCIÓN DE DATOS (Compatible con bi34.json)
# ============================================

def extract_structured_data(
    document_text: str,
    form_schema: Dict,
    custom_instructions: Optional[str] = None
) -> Dict:
    """
    Extrae datos estructurados de un documento legal mexicano.
    
    Soporta múltiples esquemas dinámicos provenientes de act_forms_catalog:
    - BI1 (Traslativo de dominio)
    - BI34 (Constitución de patrimonio familiar)
    - BI58 (Traslativo de primera inscripción)
    - BI32 (Subdivisión)
    - BI6 (Adjudicación por remate)
    - Y otros tipos de actos mexicanos
    
    Args:
        document_text: Contenido del documento (Markdown o texto plano)
        form_schema: Esquema JSON dinámico de act_forms_catalog[jsconfforma]
        custom_instructions: Instrucciones adicionales (opcional)
    
    Returns:
        Dict: Datos extraídos con estructura de UUIDs
        
    Example:
        >>> import json
        >>> from db import get_catalog_schema
        >>> 
        >>> schema = get_catalog_schema("BI34")  # Obtener de act_forms_catalog
        >>> result = extract_structured_data(doc_text, schema)
        >>> print(result["uuid"]["value"])  # Acceder según estructura dinámica
    """
    
    llm = init_localai_llm()
    
    template = """
    Eres un analista experto en documentos legales mexicanos (Actas y Escrituras).
    Se te proporciona:
    1. Un documento extraído en Markdown
    2. Un esquema de formulario dinámico JSON con campos requeridos (identificados por UUID)
    
    TAREA CRÍTICA:
    - Analiza PROFUNDAMENTE el esquema (puede tener múltiples niveles de anidación)
    - Busca en el documento la información correspondiente a cada UUID
    - Para campos repetibles (matrices): extrae TODAS las instancias, no solo una
    - Retorna ÚNICAMENTE JSON válido
    
    {custom_inst}
    
    DOCUMENTO:
    {document}
    
    ESQUEMA A EXTRAER:
    {schema}
    
    RESPUESTA (Solo JSON válido):
    """
    
    system_message = """
    Eres un especialista en extracción de datos de documentos legales mexicanos.
    Extraes información con máxima precisión, evitando alucinaciones.
    Mantienes fidelidad total a los datos del documento.
    """
    
    custom_inst_text = f"INSTRUCCIONES ADICIONALES:\n{custom_instructions}" if custom_instructions else ""
    
    prompt = PromptTemplate(
        input_variables=["document", "schema", "custom_inst"],
        template=template
    )
    
    chain = prompt | llm
    
    # Limitar tamaño del documento para evitar timeout
    safe_doc = document_text[:1000000]
    
    response = chain.invoke({
        "document": safe_doc,
        "schema": json.dumps(form_schema, indent=2, ensure_ascii=False),
        "custom_inst": custom_inst_text
    })
    
    # Parsear respuesta
    response_text = response.content.strip()
    
    # Limpiar markdown code blocks
    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0]
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0]
    
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        print(f"⚠️ Error parseando JSON: {e}")
        print(f"Respuesta: {response_text[:500]}")
        return {}


# ============================================
# ANÁLISIS MULTIMODAL (IMÁGENES)
# ============================================

def extract_from_image(
    image_path: str,
    task_description: str = "Extrae el contenido de texto del documento"
) -> str:
    """
    Extrae contenido de una imagen usando Granite Vision.
    
    Args:
        image_path: Ruta a archivo de imagen (PNG, JPG, TIFF)
        task_description: Descripción de la tarea
    
    Returns:
        str: Texto extraído de la imagen
        
    Example:
        >>> text = extract_from_image("documento.jpg")
        >>> print(text[:200])
    """
    
    llm = init_localai_llm()
    
    # LocalAI soporta base64 inline
    import base64
    
    with open(image_path, "rb") as img_file:
        image_b64 = base64.b64encode(img_file.read()).decode("utf-8")
    
    # Detectar tipo de imagen
    ext = image_path.lower().split(".")[-1]
    media_type = f"image/{ext}" if ext != "jpg" else "image/jpeg"
    
    # Construir mensaje compatible con OpenAI
    message = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": f"Tarea: {task_description}\n\nPor favor, extrae TODO el contenido de texto del documento (incluye números, fechas, nombres, direcciones, montos, etc.)."
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{image_b64}"
                }
            }
        ]
    }
    
    # Usar API directamente (ChatOpenAI soporta esto)
    response = llm.invoke([message])
    
    return response.content


# ============================================
# BATCH PROCESSING
# ============================================

def batch_extract_forms(
    documents: List[Dict[str, str]],
    form_schema: Dict,
    max_workers: int = 4
) -> List[Dict]:
    """
    Procesa múltiples documentos en paralelo (para 100+ formularios).
    
    Cada documento puede tener un esquema diferente proveniente de act_forms_catalog.
    
    Args:
        documents: Lista de diccionarios con keys 'id' y 'content'
                   Cada uno con esquema potencialmente diferente
        form_schema: Esquema común (si aplica) o sobrescribible por documento
        max_workers: Número de workers paralelos (recomendado: cores disponibles / 2)
    
    Returns:
        List[Dict]: Resultados de extracción para cada documento
        
    Example:
        >>> docs = [
        ...     {"id": "BI1_001", "content": "Documento BI1...", "schema": schema_bi1},
        ...     {"id": "BI34_002", "content": "Documento BI34...", "schema": schema_bi34}
        ... ]
        >>> results = batch_extract_forms(docs, form_schema=None, max_workers=4)
        >>> for r in results:
        ...     print(f"{r['id']}: {'✓' if r['success'] else '✗'}")
    """
    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    def process_single(doc: Dict) -> Dict:
        """Procesa un documento individual"""
        try:
            extracted = extract_structured_data(
                doc["content"],
                form_schema
            )
            return {
                "id": doc["id"],
                "success": True,
                "data": extracted,
                "error": None
            }
        except Exception as e:
            return {
                "id": doc["id"],
                "success": False,
                "data": None,
                "error": str(e)
            }
    
    results = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Enviar trabajos
        futures = {
            executor.submit(process_single, doc): doc["id"]
            for doc in documents
        }
        
        # Recolectar resultados
        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
                
                status = "✓" if result["success"] else "✗"
                print(f"{status} {result['id']}: {'OK' if result['success'] else result['error']}")
            except Exception as e:
                print(f"✗ Error procesando workers: {e}")
    
    return results


# ============================================
# CHAIN CONFIGURATION (LangChain)
# ============================================

class ExtractorChain:
    """
    Chain especializado para extracción de datos legales.
    """
    
    def __init__(self, form_schema: Dict):
        self.llm = init_localai_llm()
        self.form_schema = form_schema
        self.prompt = self._build_prompt()
        self.chain = self.prompt | self.llm
    
    def _build_prompt(self) -> PromptTemplate:
        """Construye optimized prompt para Granite Vision"""
        return PromptTemplate(
            input_variables=["document"],
            template="""
            [INST] <<SYS>>
            Eres especialista en documentos legales mexicanos (Actas y Escrituras).
            Tarea: Extrae información estructurada en JSON.
            Restricciones:
            - Temperatura: 0.1 (precisión máxima)
            - Evita alucinaciones
            - Mantén fidelidad a datos reales
            - Procesa múltiples niveles de anidación
            - Para matrices: extrae TODAS las instancias
            <</SYS>>
            
            Documento a analizar:
            {document}
            
            Esquema a usar:
            {schema}
            
            Extrae y retorna JSON válido: [/INST]
            """.format(
                schema=json.dumps(self.form_schema, indent=2, ensure_ascii=False)
            )
        )
    
    def invoke(self, document: str) -> Dict:
        """Ejecuta la extracción"""
        response = self.chain.invoke({"document": document})
        
        # Parsear respuesta
        text = response.content.strip()
        text = text.split("```json")[1].split("```")[0] if "```json" in text else text
        
        try:
            return json.loads(text)
        except:
            return {}


# ============================================
# TESTING & VALIDATION
# ============================================

if __name__ == "__main__":
    
    # Test 1: Conexión básica
    print("=" * 60)
    print("TEST 1: Validar conexión a LocalAI")
    print("=" * 60)
    
    try:
        llm = init_localai_llm()
        response = llm.invoke("¿Cuál es la capital de México?")
        print(f"✓ Conexión OK\nRespuesta: {response.content[:100]}...\n")
    except Exception as e:
        print(f"✗ Error de conexión: {e}\n")
    
    # Test 2: Extracción simple
    print("=" * 60)
    print("TEST 2: Extracción de datos (Multi-Esquema act_forms_catalog)")
    print("=" * 60)
    
    test_doc = """
    ACTA DE VENTA Y COMPRA (BI1 - Traslativo de dominio)
    Número: 12345
    Fecha: 15 de marzo de 2026
    Notario: Lic. Juan Pérez García
    
    Comparecen:
    - VENDEDOR: Carlos López Martínez, cédula 001234567
    - COMPRADOR: María García López, cédula 007654321
    
    Descripción de bien:
    Propiedad ubicada en Avenida Paseo de la Reforma 505, CDMX
    """
    
    # Simular esquema dinámico de act_forms_catalog
    test_schema = {
        "acta_numero": {"type": "text", "label": "Número de Acta"},
        "acta_fecha": {"type": "date", "label": "Fecha"},
        "notario_nombre": {"type": "text", "label": "Nombre del Notario"},
        "vendedor_nombre": {"type": "text", "label": "Nombre Vendedor"},
        "comprador_nombre": {"type": "text", "label": "Nombre Comprador"},
        "bien_ubicacion": {"type": "text", "label": "Ubicación del Bien"}
    }
    
    try:
        result = extract_structured_data(test_doc, test_schema)
        print("✓ Extracción completada")
        print(f"Resultado: {json.dumps(result, indent=2, ensure_ascii=False)}\n")
    except Exception as e:
        print(f"✗ Error en extracción: {e}\n")
    
    print("=" * 60)
    print("Testing completado")
    print("=" * 60)

