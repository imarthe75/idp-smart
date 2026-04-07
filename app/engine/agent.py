import os
import re
import json
import base64
import time
import uuid
import logging
from functools import wraps
from core.config import settings
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain.prompts import PromptTemplate
from langchain_core.messages import HumanMessage
from engine.ensemble import get_ensemble_llm

def create_simplified_json(extracted_data: dict, schema: dict) -> dict:
    """
    Transforma uuid→value pairs extraídos por el LLM a label→value pairs humanizados.

    Reglas:
    - Todos los UUIDs (incluso en dicts anidados) se reemplazan por su label del schema.
    - Los valores null/None se OMITEN del resultado final para mantener el JSON limpio.
    - Si ningún campo tiene valor real, retorna {"status": "Sin datos extraídos"}.
    """
    uuid_to_label_map: dict = {}

    # ── Paso 1: Construir mapa UUID→Label recorriendo todo el schema ──────────
    def build_uuid_map(node):
        if isinstance(node, dict):
            u = node.get("uuid")
            l = node.get("label")
            if u and l:
                uuid_to_label_map[u] = l
            for v in node.values():
                build_uuid_map(v)
        elif isinstance(node, list):
            for item in node:
                build_uuid_map(item)

    build_uuid_map(schema)

    # ── Paso 2: Transformar extracted_data resolviendo UUIDs y filtrando nulls ─
    _EMPTY = (None, "", [], {})

    def resolve_key(k: str) -> str:
        """Reemplaza un UUID por su label; si no está en el mapa, lo deja tal cual."""
        return uuid_to_label_map.get(k, k)

    def transform_value(value):
        if isinstance(value, dict):
            result = {}
            for k, v in value.items():
                transformed = transform_value(v)
                if transformed not in _EMPTY:          # omitir nulls anidados
                    result[resolve_key(k)] = transformed
            return result or None                       # dict vacío → None (se filtrará)
        elif isinstance(value, list):
            items = [transform_value(i) for i in value if transform_value(i) not in _EMPTY]
            return items or None
        return value

    simplified: dict = {}
    for k, v in extracted_data.items():
        transformed = transform_value(v)
        if transformed not in _EMPTY:                  # omitir campos nulos en raíz
            simplified[resolve_key(k)] = transformed

    return simplified if simplified else {"status": "Sin datos extraídos"}

def get_llm(llm_provider: str = None, llm_model: str = None):
    """Retorna instancia LLM con soporte para overrides."""
    provider = llm_provider or settings.llm_provider
    model_name = llm_model or settings.current_llm_model
    
    try:
        if provider == "google":
            return ChatGoogleGenerativeAI(model=model_name, google_api_key=settings.google_api_key, temperature=0)
        elif provider == "openai":
            return ChatOpenAI(model=model_name, api_key=settings.openai_api_key, base_url=settings.openai_base_url, temperature=0)
        elif provider == "anthropic":
            return ChatAnthropic(model=model_name, api_key=settings.anthropic_api_key, temperature=0)
        elif provider == "groq":
            return ChatGroq(model=model_name, api_key=settings.groq_api_key, temperature=0)
        elif provider == "alibaba":
            return ChatOpenAI(model=model_name, api_key=settings.alibaba_api_key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", temperature=0)
        elif provider == "ollama":
            return ChatOllama(base_url=settings.ollama_base_url, model=settings.ollama_model, temperature=0)
    except Exception as e:
        print(f"Error cargando LLM {provider}: {e}")
        return ChatGoogleGenerativeAI(model="gemini-1.5-flash", google_api_key=settings.google_api_key)
    
    return ChatGoogleGenerativeAI(model="gemini-1.5-flash", google_api_key=settings.google_api_key)

def extract_form_data(markdown_content: str, json_schema: dict, visual_analysis: str = "", image_paths: list = [], llm_provider: str = None, llm_model: str = None, act_id: str = None) -> dict:
    """Extrae datos usando el LLM seleccionado con contexto legal expandido."""
    try:
        llm = get_llm(llm_provider, llm_model)
        
        # Cargar contexto legal si existe
        legal_hint = ""
        if act_id:
            try:
                import json
                base_dir = os.path.dirname(__file__)
                ctx_path = os.path.join(base_dir, "legal_context.json")
                if os.path.exists(ctx_path):
                    with open(ctx_path, 'r', encoding='utf-8') as f:
                        all_ctx = json.load(f)
                        act_info = all_ctx.get(act_id.lower())
                        if act_info:
                            sections = sorted(list(set([i['section'] for i in act_info])))
                            legal_hint = f"\n--- CONTEXTO LEGAL DEL ACTO ({act_id.upper()}) ---\n"
                            legal_hint += f"Este documento debe contener información para las siguientes secciones:\n"
                            legal_hint += "- " + "\n- ".join(sections[:20]) + "\n"
            except: pass

        schema_min = minify_schema(json_schema)
        schema_prompt = json.dumps(schema_min, indent=2)
        
        prompt = PromptTemplate.from_template("""
        IDENTIDAD: Eres un Analista Registral Experto en el Registro Público de la Propiedad.
        MISIÓN: Extraer valores precisos de documentos notariales para inyectarlos en una Base de Datos técnica.
        
        {legal_hint}
        
        REGLAS CRÍTICAS:
        1. NO MODIFIQUES EL ESQUEMA. Retorna solo los valores para los UUIDs proporcionados.
        2. EXTRACCIÓN LITERAL: No resumas nombres. Extrae tal cual aparece (Mayúsculas, acentos).
        3. DATOS VACÍOS: Si un campo no existe, déjalo como null o cadena vacía.
        4. ESTRUCTURA DE SALIDA: Retorna ÚNICAMENTE un JSON plano {{ "UUID": "VALOR" }}.
        
        ESQUEMA TÉCNICO (UUIDs a llenar):
        {schema}
        
        CONTENIDO DEL DOCUMENTO:
        {content}
        
        INSTRUCCIÓN FINAL: Tu respuesta debe ser estrictamente el objeto JSON. Sin explicaciones.
        """)
        
        chain = prompt | llm
        
        # --- LOGICA DE PROCESAMIENTO POR TROZOS (CHUNKING) PARA DOCUMENTOS LARGOS ---
        # 60,000 caracteres ~= 15k-20k tokens (margen de seguridad para la mayoría de modelos)
        CHUNK_LIMIT = 60000 
        OVERLAP = 5000
        
        if len(markdown_content) <= CHUNK_LIMIT:
            # Procesamiento estándar (un solo bloque)
            response = chain.invoke({
                "schema": schema_prompt,
                "content": markdown_content,
                "legal_hint": legal_hint
            })
            return {"fields": parse_llm_json(response)}
        else:
            # Procesamiento iterativo por trozos
            print(f"📦 [AGENT] Documento largo ({len(markdown_content)} chars). Iniciando extracción por fragmentos...")
            full_extracted_data = {}
            
            # Dividir en segmentos con solapamiento
            chunks = []
            for i in range(0, len(markdown_content), CHUNK_LIMIT - OVERLAP):
                chunks.append(markdown_content[i:i + CHUNK_LIMIT])
            
            for idx, chunk_content in enumerate(chunks):
                print(f"🧠 [AGENT] Procesando fragmento {idx+1}/{len(chunks)} ({len(chunk_content)} chars)...")
                try:
                    res_chunk = chain.invoke({
                        "schema": schema_prompt,
                        "content": chunk_content,
                        "legal_hint": legal_hint
                    })
                    chunk_data = parse_llm_json(res_chunk)
                    # Merge inteligente: Conservar valores no nulos
                    for k, v in chunk_data.items():
                        if v and not full_extracted_data.get(k):
                            full_extracted_data[k] = v
                except Exception as ce:
                    print(f"⚠️ [AGENT] Error en fragmento {idx+1}: {ce}")
            
            return {"fields": full_extracted_data}
            
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "quota" in error_msg.lower():
            print(f"⚠️ [QUOTA EXCEEDED] Reintentando en 60s por error 429: {error_msg}")
            time.sleep(60)
            return extract_form_data(markdown_content, json_schema, visual_analysis, image_paths, llm_provider, llm_model, act_id)
        
        print(f"Error en extracción experta: {e}")
        return {"fields": {}}

def parse_llm_json(response) -> dict:
    """Helper para limpiar y parsear la respuesta del LLM."""
    text = response.content if hasattr(response, 'content') else str(response)
    
    # Limpieza robusta de JSON (maneja bloques markdown ```json)
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except:
            try:
                # Reparación agresiva si falla el JSON simple
                cleaned = re.sub(r'//.*', '', match.group(0)) # quitar comentarios
                return json.loads(cleaned)
            except:
                return {}
    return {}

# Mantener funciones auxiliares necesarias (minify_schema, etc)
def minify_schema(schema):
    import copy
    m = {}
    if isinstance(schema, dict):
        if "uuid" in schema: m["uuid"] = schema["uuid"]
        if "label" in schema: m["label"] = schema["label"]
        if "controls" in schema: m["controls"] = [{"uuid": c["uuid"], "label": c["label"]} for c in schema["controls"]]
        if "containers" in schema: m["containers"] = [minify_schema(c) for c in schema["containers"]]
        # Recurse other keys
        for k,v in schema.items():
            if k not in ["uuid", "label", "controls", "containers", "style", "width", "visible"]:
                m[k] = minify_schema(v)
    elif isinstance(schema, list): return [minify_schema(i) for i in schema]
    return m or schema
