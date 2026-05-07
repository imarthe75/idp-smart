import os
import re
import json
import base64
import time
import uuid
import logging
from functools import wraps
from core.config import settings
from google import genai
from google.genai import types
from engine.ensemble import get_ensemble_llm
from engine.heuristics import heuristics

def create_simplified_json(extracted_data: dict, schema: dict) -> dict:
    """
    Transforma data (plana o anidada) a JSON humanizado mapeando UUIDs a Labels.
    """
    u_to_l: dict = {}

    def build_map(node):
        if isinstance(node, dict):
            u = node.get("uuid")
            l = node.get("label") or node.get("name")
            if u:
                u_str = str(u).lower().strip()
                lbl = str(l).strip() if l else ""
                u_to_l[u_str] = lbl if lbl else f"Campo_{u_str[:4]}"
            for v in node.values():
                build_map(v)
        elif isinstance(node, list):
            for i in node: build_map(i)

    build_map(schema)

    _EMPTY = (None, "", [], {}, "null", "NULL")

    def resolve(k):
        ks = str(k).lower().strip()
        return u_to_l.get(ks, k)

    def simplify(data):
        if isinstance(data, dict):
            # Caso 1: Estructura {uuid: ..., value: ...}
            if "uuid" in data and "value" in data:
                val = simplify(data["value"])
                if val not in _EMPTY:
                    return {resolve(data["uuid"]): val}
                return None
            
            # Caso 2: Diccionario de UUIDs -> Valores
            res = {}
            for k, v in data.items():
                if k in ("containers", "controls", "fields") and isinstance(v, list):
                    for item in v:
                        s = simplify(item)
                        if isinstance(s, dict):
                            # MEJORA: Si la llave ya existe, convertir a lista para no perder datos repetitivos
                            for sk, sv in s.items():
                                if sk in res:
                                    if not isinstance(res[sk], list):
                                        res[sk] = [res[sk]]
                                    if isinstance(sv, list):
                                        res[sk].extend(sv)
                                    else:
                                        res[sk].append(sv)
                                else:
                                    res[sk] = sv
                elif k not in ("uuid", "label", "name", "type", "repetitiva", "orden"):
                    val = simplify(v)
                    if val not in _EMPTY:
                        # Similar a arriba, manejar colisiones por labels iguales (común en secciones repetitivas)
                        label = resolve(k)
                        if label in res:
                             if not isinstance(res[label], list):
                                res[label] = [res[label]]
                             if isinstance(val, list): res[label].extend(val)
                             else: res[label].append(val)
                        else:
                             res[label] = val
            return res if res else None
        
        elif isinstance(data, list):
            items = [simplify(i) for i in data]
            items = [i for i in items if i not in _EMPTY]
            # Si todos los items son dicts con la misma única llave, colapsar? 
            # Mejor dejar la lista para que sea fiel a la repetición.
            return items if items else None
        
        return data

    out = simplify(extracted_data)
    if not out: return {"status": "Sin datos extraídos"}
    return out

def get_llm(llm_provider: str = None, llm_model: str = None):
    """Retorna instancia LLM o cliente GenAI según el proveedor."""
    provider = llm_provider or settings.llm_provider
    model_name = llm_model or settings.current_llm_model
    common = {"temperature": 0, "max_tokens": 8192}
    
    # Prioridad: Vertex AI nativo vía GenAI SDK
    if provider in ("vertex", "google"):
        project = settings.gcp_project_id
        location = settings.gcp_location
        print(f"✨ [AGENT] Inicializando GenAI SDK (Vertex AI) - Project: {project}")
        return genai.Client(vertexai=True, project=project, location=location)

    # Fallback a LangChain solo si es estrictamente necesario para otros providers
    try:
        if provider == "openai":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=model_name, api_key=settings.openai_api_key, base_url=settings.openai_base_url, **common)
        elif provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=model_name, api_key=settings.anthropic_api_key, **common)
        elif provider == "groq":
            from langchain_groq import ChatGroq
            return ChatGroq(model=model_name, api_key=settings.groq_api_key, **common)
        elif provider == "nvidia":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=model_name, 
                api_key=settings.nvidia_nim_api_key, 
                base_url=settings.nvidia_nim_base_url, 
                **common
            )
    except ImportError:
        print(f"⚠️ [AGENT] Provider {provider} solicitado pero dependencias no instaladas.")
    
    # Default final
    return genai.Client(vertexai=True, project=settings.gcp_project_id, location=settings.gcp_location)

def get_flat_schema(node, flat_dict=None):
    if flat_dict is None: flat_dict = {}
    if isinstance(node, dict):
        u = node.get("uuid")
        l = node.get("label") or node.get("name")
        t = node.get("type", "field")
        r = node.get("repetitiva", False)
        
        if u and l:
            # Enviar info enriquecida al LLM
            flat_dict[str(u)] = {
                "label": l,
                "type": t,
                "repetitivo": r
            }
        
        # Recursión sobre hijos
        for key in ["controls", "fields", "containers"]:
            if key in node and isinstance(node[key], list):
                for child in node[key]:
                    get_flat_schema(child, flat_dict)
        # Fallback para otras llaves anidadas
        for k, v in node.items():
            if k not in ("controls", "fields", "containers") and isinstance(v, (dict, list)):
                get_flat_schema(v, flat_dict)
                
    elif isinstance(node, list):
        for i in node:
            get_flat_schema(i, flat_dict)
    return flat_dict

def load_legal_context(act_id: str) -> str:
    if not act_id: return ""
    try:
        base_dir = os.path.dirname(__file__)
        ctx_path = os.path.join(base_dir, "legal_context.json")
        if os.path.exists(ctx_path):
            with open(ctx_path, 'r', encoding='utf-8') as f:
                all_ctx = json.load(f)
                act_info = all_ctx.get(act_id.lower())
                if act_info:
                    sections = sorted(list(set([i['section'] for i in act_info])))
                    hint = f"\n--- CONTEXTO LEGAL DEL ACTO ({act_id.upper()}) ---\n"
                    hint += f"Este documento debe contener información para las siguientes secciones:\n"
                    hint += "- " + "\n- ".join(sections[:20]) + "\n"
                    return hint
    except: pass
    return ""

def _build_prompt(schema_prompt: str, content: str, legal_hint: str, values_only: bool = False, is_native_pdf: bool = False) -> str:
    if values_only:
        instruct = """Extrae la información del documento adjunto y genera ÚNICAMENTE un objeto JSON PLANO.
Llaves: El UUID del campo.
Valores: El dato encontrado.
Si el campo es REPETITIVO, retorna una LISTA de objetos.

REGLAS DE LIMPIEZA MUY IMPORTANTES:
1. Corrige errores de espaciado: Si ves nombres pegados como "FELIPEANTONIO" o "CHALELOPEZ", sepáralos lógicamente a "FELIPE ANTONIO" y "CHALE LOPEZ".
2. Elimina duplicados exactos o muy similares en listas.
3. Corrige problemas de codificación (ej. "DirecciÃ³n" -> "Dirección", "NÃºmero" -> "Número").
4. Estandariza todo el texto a formato Título (Capitalizado) cuando sean nombres propios o estados (ej. "Quintana Roo" en vez de "QUINTANA ROO")."""
        if is_native_pdf:
            instruct += "\nIMPORTANTE: Como estás procesando el PDF directamente, añade una clave extra 'markdown_transcript' con TODO el texto del documento transcrito fielmente en formato Markdown."
    else:
        instruct = """Extrae la información e inyéctala en el JSON-TEMPLATE. 
Por favor limpia los datos, corrige espacios faltantes en los nombres y elimina duplicados."""

    content_label = "CONTENIDO PDF (Nativo)" if is_native_pdf else "CONTENIDO OCR"
    return f"""misión: extraer datos notariales.
{legal_hint}
ESQUEMA: {schema_prompt}
INSTRUCCIÓN: {instruct}
{content_label}: {content if not is_native_pdf else 'Documento adjunto en GCS'}
RETORNA ÚNICAMENTE EL JSON."""

def extract_form_data(markdown_content: str, json_schema: dict, visual_analysis: str = "", image_paths: list = [], llm_provider: str = None, llm_model: str = None, act_id: str = None, gcs_uri: str = None, _retry_count: int = 0) -> dict:
    import traceback as tb
    try:
        # H5: Clasificación Heurística del Documento
        heur_type = heuristics.classify_document(markdown_content)
        print(f"📊 [HEURISTICS] Tipo detectado: {heur_type}")
        
        llm = get_llm(llm_provider, llm_model)
        VALUES_ONLY_MODE = True 

        if VALUES_ONLY_MODE:
            schema_data = get_flat_schema(json_schema)
            schema_prompt = json.dumps(schema_data, indent=2)
        else:
            schema_min = minify_schema(json_schema)
            schema_prompt = json.dumps(schema_min, indent=2)

        legal_hint = load_legal_context(act_id)
        if heur_type != "desconocido":
            legal_hint += f"\nHEURÍSTICA: Este documento ha sido pre-clasificado como {heur_type.upper()}."

        if _retry_count == 0:
            CHUNK_LIMIT = 400000 if llm_provider == "vertex" else 55000
        elif _retry_count == 1:
            CHUNK_LIMIT = 150000
        else:
            CHUNK_LIMIT = 55000
        OVERLAP     = 4000

        def _invoke_llm(content_chunk: str) -> dict:
            prompt_text = _build_prompt(schema_prompt, content_chunk, legal_hint, values_only=VALUES_ONLY_MODE, is_native_pdf=bool(gcs_uri))
            
            # Caso GenAI SDK (Vertex AI nativo)
            if isinstance(llm, genai.Client):
                model_to_use = llm_model or settings.gemini_model or "gemini-1.5-flash"
                print(f"🚀 [AGENT] Generando con GenAI SDK (Vertex): {model_to_use}")
                
                contents = [prompt_text]
                if gcs_uri:
                    contents.append(types.Part.from_uri(file_uri=gcs_uri, mime_type="application/pdf"))
                
                response = llm.models.generate_content(
                    model=model_to_use,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                raw = response.text
            else:
                # Caso LangChain
                from langchain_core.messages import HumanMessage
                messages = [HumanMessage(content=prompt_text)]
                response = llm.invoke(messages)
                raw = response.content if hasattr(response, "content") else str(response)
            
            return parse_llm_json(raw)

        extracted_raw = {}
        if len(markdown_content) <= CHUNK_LIMIT:
            extracted_raw = _invoke_llm(markdown_content)
        else:
            print(f"📦 [AGENT] Documento largo ({len(markdown_content)} chars). Chunking activado…")
            chunks = [markdown_content[i:i + CHUNK_LIMIT] for i in range(0, len(markdown_content), CHUNK_LIMIT - OVERLAP)]
            for idx, chunk in enumerate(chunks):
                try:
                    chunk_data = _invoke_llm(chunk)
                    for k, v in chunk_data.items():
                        if v:
                            if k not in extracted_raw:
                                extracted_raw[k] = v
                            else:
                                if isinstance(extracted_raw[k], list) and isinstance(v, list):
                                    extracted_raw[k].extend(v)
                                elif not extracted_raw[k]:
                                    extracted_raw[k] = v
                except Exception as ce:
                    print(f"⚠️ [AGENT] Error fragmento {idx+1}: {ce}")
                    error_msg = str(ce).lower()
                    if "429" in error_msg or "quota" in error_msg or "rate_limit" in error_msg or "resource_exhausted" in error_msg or "resource exhausted" in error_msg:
                        raise ce
        
        # H1, H7: Post-procesamiento Heurístico (Limpieza y Priorización)
        cleaned_data = heuristics.post_process_extraction(extracted_raw)
        
        # H6: Validación de Consistencia
        alerts = heuristics.validate_consistency(cleaned_data)
        if alerts:
            print(f"⚠️ [HEURISTICS] Alertas de consistencia: {alerts}")
            cleaned_data["_heuristics_alerts"] = alerts

        result_dict = {"fields": cleaned_data}
        if "markdown_transcript" in extracted_raw:
            result_dict["markdown_transcript"] = extracted_raw["markdown_transcript"]
            
        return result_dict

    except Exception as e:
        error_msg = str(e).lower()
        if "429" in error_msg or "quota" in error_msg or "rate_limit" in error_msg or "resource_exhausted" in error_msg or "resource exhausted" in error_msg:
            if _retry_count >= 2:
                print("❌ [AGENT] Demasiados reintentos por Quota. Abortando.")
                return {"fields": {}}
            print(f"⚠️ [QUOTA] Rate limit alcanzado. Reduciendo Chunk Limit a {150000 if _retry_count == 0 else 55000} y esperando 10s... (Reintento {_retry_count+1})")
            time.sleep(10)
            return extract_form_data(markdown_content, json_schema, visual_analysis, image_paths, llm_provider, llm_model, act_id, gcs_uri, _retry_count + 1)
        print(f"❌ [AGENT] Error crítico: {error_msg}")
        return {"fields": {}}

def parse_llm_json(text_or_response) -> dict:
    text = text_or_response
    if hasattr(text_or_response, "content"):
        text = text_or_response.content
    text = str(text).strip()
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if code_block:
        text = code_block.group(1)
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1 or end <= start: return {}
    raw_json = text[start:end + 1]
    try:
        cleaned = re.sub(r"//[^\n]*", "", raw_json)
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", cleaned)
        parsed = json.loads(cleaned)
    except:
        try: parsed = json.loads(raw_json)
        except: return {}
    
    return _flatten_llm_response(parsed)

def _flatten_llm_response(data) -> dict:
    flat: dict = {}
    UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

    def _extract(node):
        if isinstance(node, dict):
            u = node.get("uuid")
            v = node.get("value")
            
            # 1. Si el dict tiene formato {uuid, value}, extraerlo
            if u and v is not None and v != "" and v != "null":
                u_str = str(u).lower().strip()
                if u_str in flat:
                    if not isinstance(flat[u_str], list): flat[u_str] = [flat[u_str]]
                    flat[u_str].append(v)
                else:
                    flat[u_str] = v
            
            # 2. Recorrer controles y contenedores estándar
            if "containers" in node and isinstance(node["containers"], list):
                for container in node["containers"]: _extract(container)
            if "fields" in node and isinstance(node["fields"], list):
                for f in node["fields"]: _extract(f)
            
            # 3. Recorrer llaves que parezcan UUIDs (formato plano)
            for k, val in node.items():
                ks = str(k).lower().strip()
                if ks not in ("uuid", "value", "label", "controls", "fields", "containers", "repetitiva", "orden"):
                    if UUID_RE.match(ks) and val is not None and val != "" and val != "null":
                        if ks in flat:
                            # Evitar colisiones: si ya existe, convertir a lista
                            if not isinstance(flat[ks], list): flat[ks] = [flat[ks]]
                            if isinstance(val, list): flat[ks].extend(val)
                            else: flat[ks].append(val)
                        else:
                            flat[ks] = val
                    elif isinstance(val, (dict, list)):
                        _extract(val)

        elif isinstance(node, list):
            for item in node:
                _extract(item)

    _extract(data)
    
    # Fallback para dicts planos puros que no pasaron por _extract correctamente
    if not flat and isinstance(data, dict):
        return {str(k).lower(): v for k, v in data.items() if v is not None and v != ""}
    
    return flat

def minify_schema(schema) -> dict:
    if isinstance(schema, list):
        return [minify_schema(i) for i in schema]
    if not isinstance(schema, dict):
        return schema
    m: dict = {}
    if "uuid" in schema: m["uuid"] = schema["uuid"]
    if "label" in schema: m["label"] = schema["label"]
    if "type" in schema: m["type"] = schema["type"]
    if schema.get("repetitiva"): m["repetitiva"] = True
    if "type" in schema and schema["type"] not in ("container", "campos_repetitivos"):
        m["value"] = None
    for key in ["controls", "fields", "containers"]:
        val = schema.get(key)
        if val is not None:
            m[key] = [minify_schema(c) for c in (val if isinstance(val, list) else []) if isinstance(c, dict) and (c.get("uuid") or key == "containers")]
    return m if m else schema
