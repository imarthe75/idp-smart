import os
import re
import json
import base64
from core.config import settings
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain.prompts import PromptTemplate
from langchain_core.messages import HumanMessage
from engine.ensemble import get_ensemble_llm  # NUEVO: Soporte ensemble

def create_simplified_json(extracted_data: dict, schema: dict) -> dict:
    """
    Transforma uuid → value pairs a label → value pairs humanizados.
    NUNCA retorna null: siempre retorna un dict válido, aunque sea vacío.
    
    Este es SOLO un transformador de presentación, no cambia los datos.
    """
    simplified = {}
    uuid_to_label_map = {}
    
    # Fase 1: Construir mapa completo de uuid → label desde el esquema
    def build_uuid_map(node):
        """Recorre esquema y mapea uuid → label"""
        if isinstance(node, dict):
            uuid_val = node.get("uuid")
            label_val = node.get("label")
            if uuid_val and label_val:
                uuid_to_label_map[uuid_val] = label_val
            
            # Continuar en propiedades
            for v in node.values():
                build_uuid_map(v)
        elif isinstance(node, list):
            for item in node:
                build_uuid_map(item)
    
    build_uuid_map(schema)
    
    # Fase 2: Transformar datos extraídos usando el mapa
    def transform_value(value):
        """Convierte valor recursivamente: si es dict con uuids, substituye por labels"""
        if isinstance(value, dict):
            transformed = {}
            for k, v in value.items():
                # Si la clave es un uuid, usa su label
                label_key = uuid_to_label_map.get(k, k)
                transformed[label_key] = transform_value(v)
            return transformed
        elif isinstance(value, list):
            # Para arrays, transforma cada elemento
            return [transform_value(item) for item in value]
        else:
            # Valor primitivo
            return value
    
    # Fase 3: Inyectar datos transformados
    for uuid_key, value in extracted_data.items():
        label_key = uuid_to_label_map.get(uuid_key, uuid_key)
        transformed_value = transform_value(value)
        simplified[label_key] = transformed_value
    
    # Fase 4: Garantizar que NUNCA retorna None/null
    # Si está vacío, retorna estructura mínima válida
    if not simplified:
        simplified = {
            "Estado": "Sin datos extraídos del documento",
            "Nota": "Verifica que el documento contenga la información esperada"
        }
    
    return simplified

def get_llm():
    """
    Instancia el LLM configurado en Settings.
    Soporta Google Gemini, Ollama (Legacy), LocalAI, RunPod y Ensemble.
    Valores aceptados para LLM_PROVIDER: gemini | google | local | localai | runpod | ollama
    """
    try:
        # ¿Usar ensemble?
        if settings.use_ensemble:
            print(f"ENSEMBLE activado: {settings.ensemble_provider} ({settings.ensemble_strategy})")
            return get_ensemble_llm(use_ensemble=True)

        # Sino, usar LLM simple
        if settings.llm_provider == "localai":
            print(f"Conectando a LocalAI en {settings.localai_url} con modelo {settings.model_reasoning}...")
            return ChatOpenAI(
                base_url=settings.localai_url,
                api_key="not-needed",
                model=settings.model_reasoning,
                temperature=settings.localai_temperature,
                max_tokens=settings.localai_max_tokens,
                timeout=settings.localai_timeout,
                model_kwargs={"response_format": {"type": "json_object"}},
                verbose=True
            )
        elif settings.llm_provider == "runpod":
            print(f"Conectando a RunPod en {settings.runpod_llm_url}...")
            return ChatOpenAI(
                base_url=settings.runpod_llm_url,
                api_key=settings.runpod_api_key,
                model=settings.llm_runpod_model,
                temperature=settings.localai_temperature,
                max_tokens=4096,
                timeout=settings.proxy_timeout_s,
                verbose=True
            )
        elif settings.llm_provider == "ollama":
            print(f"Conectando a Ollama en {settings.ollama_base_url} con modelo {settings.ollama_model}...")
            return ChatOllama(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                temperature=0
            )
        else:
            # Gemini (valores aceptados: 'gemini' o 'google' por retrocompatibilidad)
            if not settings.google_api_key:
                print("Error: No se ha configurado la GOOGLE_API_KEY.")
                return None
            os.environ["GOOGLE_API_KEY"] = settings.google_api_key
            gemini_model = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
            print(f"Conectando a Google Gemini ({gemini_model})...")
            return ChatGoogleGenerativeAI(
                model=gemini_model,
                temperature=0
            )
    except Exception as e:
        print(f"Error cargando el LLM ({settings.llm_provider}): {e}")
        return None

def minify_schema(schema):
    """
    Minimiza el esquema JSON para el LLM. Elimina toda la configuración de interfaz de usuario
    y mantiene estrictamente los UUIDs, etiquetas y jerarquía que el LLM necesita para iterar.
    """
    import copy
    
    if isinstance(schema, dict):
        # Si es el root object (usualmente tiene 'containers')
        if "containers" in schema:
            return {"containers": [minify_schema(c) for c in schema.get("containers", [])]}
        
        # Si es un contenedor
        minified = {}
        if "uuid" in schema: minified["uuid"] = schema["uuid"]
        if "label" in schema: minified["label"] = schema["label"]
        if "repetitiva" in schema: minified["repetitiva"] = schema["repetitiva"]
        
        if "controls" in schema:
            minified["controls"] = []
            for ctrl in schema["controls"]:
                m_ctrl = {}
                if "uuid" in ctrl: m_ctrl["uuid"] = ctrl["uuid"]
                if "label" in ctrl: m_ctrl["label"] = ctrl["label"]
                if "type" in ctrl: m_ctrl["type"] = ctrl["type"]
                if "maxLength" in ctrl: m_ctrl["maxLength"] = ctrl["maxLength"]
                minified["controls"].append(m_ctrl)
            return minified
            
        # Fallback para estructuras no reconocidas o anidadas
        for k, v in schema.items():
            if k not in ["style", "width", "visible", "disabled", "className", "icon", "description", "colSpan"]:
                minified[k] = minify_schema(v)
        return minified
        
    elif isinstance(schema, list):
        return [minify_schema(item) for item in schema]
        
    return schema

def convert_to_json_schema(schema_minified: dict) -> dict:
    """
    Convierte el esquema minimizado de rpp a un esquema JSON estándar
    para forzar la gramática GBNF en LocalAI.
    """
    properties = {}
    required = []

    # Procesar contenedores del root
    if "containers" in schema_minified:
        for container in schema_minified["containers"]:
            uuid = container.get("uuid")
            if not uuid: continue
            
            is_repetitive = container.get("repetitiva", False)
            
            # Sub-propiedades (controles o sub-contenedores)
            sub_props = {}
            if "controls" in container:
                for ctrl in container["controls"]:
                    ctrl_uuid = ctrl.get("uuid")
                    if ctrl_uuid:
                        sub_props[ctrl_uuid] = {"type": ["string", "number", "null"]}
            
            if is_repetitive:
                properties[uuid] = {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": sub_props
                    }
                }
            else:
                properties[uuid] = {
                    "type": "object",
                    "properties": sub_props
                }
            
            required.append(uuid)
    
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False
    }

from langchain_core.messages import HumanMessage
import base64

def extract_form_data(document_md: str, form_schema: dict, visual_analysis: str = "", image_paths: list[str] = []) -> dict:
    """
    [PASO 3: RAZONAMIENTO] Extrae datos del Markdown usando Esquema + Análisis Visual.
    Implementa la lógica de 'Fusión Legal' (Multimodal en Gemini, Texto en otros).
    """
    # Detectar si tenemos OCR real o estamos en modo visión pura
    is_ocr_available = document_md and len(document_md) > 200 and "# VISION SKIP" not in document_md
    
    transcription_instruction = ""
    if not is_ocr_available:
        transcription_instruction = """
2️⃣ TRANSCRIPCIÓN ESTRUCTURADA (MARKDOWN):
Genera una transcripción Markdown de alta fidelidad que capture el texto íntegro que observas en las imágenes.
"""
    else:
        transcription_instruction = """
2️⃣ NOTA DE TRANSCRIPCIÓN:
Ya se cuenta con OCR de alta calidad. NO generes transcripción. Enfócate 100% en la precisión de los campos JSON.
"""

    template = """
EXTRACCIÓN DE EXPERTO LEGAL (RECALL MÁXIMO) 

Eres un experto en documentos notariales mexicanos. Tu misión es extraer CADA UNO de los datos solicitados en el esquema, sin omitir ningún registro, especialmente en secciones repetitivas de SOCIOS, ASOCIADOS, APODERADOS u OTORGANTES.

--- ESTRATEGIA DE EXTRACCIÓN (REPRODUCCIÓN FIEL) ---

1️⃣ NO OMITAS REGISTROS:
- Si el documento menciona varios socios fundadores, DEBES extraer TODOS sin excepción.
- Si hay un representante legal, apoderado o gerente, inclúyelo en la sección correspondiente según su función legal.
- Si la sección es un ARRAY ([{{...}}]), extrae TODOS los bloques individuales que encuentres.

2️⃣ MAPEO SEMÁNTICO FLEXIBLE:
- "Quien transmite/vende/dona" = Enajenante. "Quien recibe/compra/adquiere" = Adquiriente.
- Formatos: NOMBRES EN MAYÚSCULAS. Fechas YYYY-MM-DD.

3️⃣ TRANSCRIPCIÓN (SI SE REQUIERE):
{transcription_instruction}

--- ESTRUCTURA DE RESPUESTA (OBLIGATORIA) ---
Responde EXCLUSIVAMENTE con un JSON válido. Respeta la jerarquía del esquema: si un contenedor (UUID) tiene controles asociados, devuelve un objeto bajo ese UUID, no una lista plana.
{{
  "fields": {{
    "uuid_contenedor_notario": {{ "uuid_estado": "VALOR", "uuid_nombre": "VALOR" }},
    "uuid_seccion_socios": [ {{ "uuid_socio": "VALOR", "uuid_rfc": "VALOR" }} ]
  }},
  "full_markdown": "SKIP o transcripción visual detallada"
}}

EVIDENCIA VISUAL:
{visual_analysis}

ESQUEMA TÉCNICO:
{form_schema}

CONTENIDO DEL DOCUMENTO:
{document_md}

JSON DE RESPUESTA:
"""
    
    llm = get_llm()
    if not llm:
        print("LLM no configurado.")
        return {}
    
    minified_schema = minify_schema(form_schema)
    schema_str = json.dumps(minified_schema, indent=2)
    # Soporte para documentos masivos (500k chars)
    # Escapar llaves para .format()
    raw_md = (document_md or "")[:500000]
    safe_markdown = raw_md.replace("{", "{{").replace("}", "}}")
    
    final_prompt = template.format(
        transcription_instruction=transcription_instruction,
        visual_analysis=visual_analysis or "No se proporcionó análisis adicional.",
        form_schema=schema_str,
        document_md=safe_markdown
    )

    import time
    start_time = time.time()

    # --- SEMÁFORO DISTRIBUIDO PARA COLA CLOUD (EVITAR 429) ---
    # Limitar a solo 2 trabajadores simultáneos hablando con Google/Cloud
    import redis
    import time
    
    use_semaphore = settings.llm_provider in ("google", "gemini", "anthropic", "openai")
    redis_client = None
    if use_semaphore:
        try:
            redis_client = redis.from_url(settings.valkey_url)
        except: pass

    response = None
    max_retries = 3
    retry_count = 0
    
    try:
        if redis_client:
            semaphore_name = f"sem:ai_concurrency:{settings.llm_provider}"
            start_wait = time.time()
            max_concurrent = int(os.environ.get("MAX_CLOUD_CONCURRENCY", "2"))
            while True:
                current_active = redis_client.get(semaphore_name)
                if not current_active or int(current_active) < max_concurrent:
                    redis_client.incr(semaphore_name)
                    redis_client.expire(semaphore_name, 300)
                    print(f"📡 [RateLimit] Slot adquirido para {settings.llm_provider}.")
                    break
                if time.time() - start_wait > 180: break
                time.sleep(2)

        # Pequeño stagger adicional para evitar ráfagas exactas
        import random
        time.sleep(random.uniform(0.1, 1.5))

        # --- BUCLE DE REINTENTO ROBUSTO ---
        while retry_count < max_retries:
            try:
                if settings.llm_provider in ("gemini", "google") and image_paths:
                    print(f"[Multimodal] Enviando {len(image_paths)} imagen(es) a Gemini (Intento {retry_count+1})...")
                    content = [{"type": "text", "text": final_prompt}]
                    for img_path in image_paths:
                        if os.path.exists(img_path):
                            with open(img_path, "rb") as f:
                                img_data = base64.b64encode(f.read()).decode("utf-8")
                                content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_data}"}})
                    response = llm.invoke([HumanMessage(content=content)])
                else:
                    print(f"[Razonamiento] Enviando prompt ({settings.llm_provider}) (Intento {retry_count+1})...")
                    response = llm.invoke([HumanMessage(content=final_prompt)])
                
                # Si llegamos aquí sin excepción, salimos del bucle de reintento
                break
            except Exception as e_retry:
                retry_count += 1
                if retry_count >= max_retries:
                    print(f"❌ [ROBUSTEZ] Error fatal tras {max_retries} intentos: {e_retry}")
                    raise e_retry
                
                backoff = (2 ** retry_count) + random.uniform(0, 1)
                print(f"⚠️ [ROBUSTEZ] Error en LLM (Intento {retry_count}): {e_retry}. Reintentando en {backoff:.2f}s...")
                time.sleep(backoff)

    finally:
        if redis_client:
            redis_client.decr(semaphore_name)
            print(f"📡 [RateLimit] Slot liberado para {settings.llm_provider}.")

    if not response or not response.content:
        print("❌ Error: Respuesta del LLM vacía o nula.")
        return {}

    elapsed = time.time() - start_time
    print(f"✅ [Razonamiento] Completado en {elapsed:.2f}s")
    
    text_response = response.content.strip()
    clean_text = text_response
    if "```json" in clean_text:
        clean_text = clean_text.split("```json")[-1].split("```")[0]
    elif "```" in clean_text:
        clean_text = clean_text.split("```")[-1].split("```")[0]
    clean_text = clean_text.strip()
    
    try:
        return json.loads(clean_text)
    except Exception as e1:
        print(f"❌ Intento 1 fallido de parseo LLM ({e1}). Intentando reparar...")
        start_idx = text_response.find('{')
        if start_idx != -1:
            json_str = text_response[start_idx:]
            open_braces = json_str.count('{') - json_str.count('}')
            open_brackets = json_str.count('[') - json_str.count(']')
            open_quotes = len(re.findall(r'(?<!\\)"', json_str)) % 2
            json_str = re.sub(r'["}\]]\s*$', '', json_str)
            if open_quotes: json_str += '"'
            if open_brackets > 0: json_str += ']' * open_brackets
            if open_braces > 0: json_str += '}' * open_braces
            try:
                extracted_json = json.loads(json_str)
                print(f"✅ JSON reparado exitosamente")
                return extracted_json
            except Exception as e2:
                print(f"❌ Intento 2 de reparación fallido: {e2}")
                raise ValueError(f"No se pudo extraer un JSON válido del LLM tras reparación: {e2}")
        
        raise ValueError("No se encontró ninguna estructura JSON '{ ... }' en la respuesta del LLM.")
