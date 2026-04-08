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
try:
    from langchain_google_vertexai import ChatVertexAI
except ImportError:
    ChatVertexAI = None
from langchain.prompts import PromptTemplate
from langchain_core.messages import HumanMessage
from engine.ensemble import get_ensemble_llm

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
            if "uuid" in data and "value" in data:
                val = simplify(data["value"])
                if val not in _EMPTY:
                    return {resolve(data["uuid"]): val}
                return None
            
            res = {}
            for k, v in data.items():
                if k in ("containers", "controls") and isinstance(v, list):
                    for item in v:
                        s = simplify(item)
                        if isinstance(s, dict): res.update(s)
                elif k not in ("uuid", "label", "name", "type", "repetitiva", "orden"):
                    val = simplify(v)
                    if val not in _EMPTY:
                        res[resolve(k)] = val
            return res if res else None
        
        elif isinstance(data, list):
            items = [simplify(i) for i in data]
            items = [i for i in items if i not in _EMPTY]
            if items and all(isinstance(i, dict) and len(i) == 1 for i in items):
                keys = set()
                for i in items: keys.update(i.keys())
                if len(keys) == 1:
                    key = list(keys)[0]
                    pass
            return items if items else None
        
        return data

    out = simplify(extracted_data)
    if not out: return {"status": "Sin datos extraídos"}
    return out

def get_llm(llm_provider: str = None, llm_model: str = None):
    """Retorna instancia LLM con soporte para overrides."""
    provider = llm_provider or settings.llm_provider
    model_name = llm_model or settings.current_llm_model
    
    common = {"temperature": 0, "max_tokens": 8192}
    
    try:
        if provider == "google":
            return ChatGoogleGenerativeAI(model=model_name, google_api_key=settings.google_api_key, **common)
        elif provider == "openai":
            return ChatOpenAI(model=model_name, api_key=settings.openai_api_key, base_url=settings.openai_base_url, **common)
        elif provider == "anthropic":
            return ChatAnthropic(model=model_name, api_key=settings.anthropic_api_key, **common)
        elif provider == "groq":
            return ChatGroq(model=model_name, api_key=settings.groq_api_key, **common)
        elif provider == "alibaba":
            return ChatOpenAI(model=model_name, api_key=settings.alibaba_api_key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", **common)
        elif provider == "vertex":
            if not ChatVertexAI:
                raise ImportError("Librería 'langchain-google-vertexai' no encontrada.")
            return ChatVertexAI(
                model_name=model_name or "gemini-1.5-flash-002",
                project=getattr(settings, "gcp_project_id", None),
                location=getattr(settings, "gcp_location", "us-central1"),
                **common
            )
        elif provider == "ollama":
            return ChatOllama(base_url=settings.ollama_base_url, model=settings.ollama_model, **common)
    except Exception as e:
        print(f"Error cargando LLM {provider}: {e}")
        return ChatGoogleGenerativeAI(model="gemini-1.5-flash", google_api_key=settings.google_api_key, **common)
    
    return ChatGoogleGenerativeAI(model="gemini-1.5-flash", google_api_key=settings.google_api_key, **common)

def get_flat_schema(node, flat_dict=None):
    if flat_dict is None: flat_dict = {}
    if isinstance(node, dict):
        u = node.get("uuid")
        l = node.get("label") or node.get("name")
        if u: flat_dict[str(u)] = l or f"Campo_{str(u)[:4]}"
        for v in node.values(): get_flat_schema(v, flat_dict)
    elif isinstance(node, list):
        for i in node: get_flat_schema(i, flat_dict)
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
Si el campo es REPETITIVO, retorna una LISTA de objetos."""
        if is_native_pdf:
            instruct += "\nIMPORTANTE: Como estás procesando el PDF directamente, añade una clave extra 'markdown_transcript' con TODO el texto del documento transcrito fielmente en formato Markdown."
    else:
        instruct = """Extrae la información e inyéctala en el JSON-TEMPLATE."""

    content_label = "CONTENIDO PDF (Nativo)" if is_native_pdf else "CONTENIDO OCR"
    return f"""misión: extraer datos notariales.
{legal_hint}
ESQUEMA: {schema_prompt}
INSTRUCCIÓN: {instruct}
{content_label}: {content if not is_native_pdf else 'Documento adjunto en GCS'}
RETORNA ÚNICAMENTE EL JSON."""

def extract_form_data(markdown_content: str, json_schema: dict, visual_analysis: str = "", image_paths: list = [], llm_provider: str = None, llm_model: str = None, act_id: str = None, gcs_uri: str = None) -> dict:
    import traceback as tb
    try:
        llm = get_llm(llm_provider, llm_model)
        VALUES_ONLY_MODE = True 

        if VALUES_ONLY_MODE:
            schema_data = get_flat_schema(json_schema)
            schema_prompt = json.dumps(schema_data, indent=2)
        else:
            schema_min = minify_schema(json_schema)
            schema_prompt = json.dumps(schema_min, indent=2)

        legal_hint = load_legal_context(act_id)
        CHUNK_LIMIT = 55000
        OVERLAP     = 4000

        def _invoke_llm(content_chunk: str) -> dict:
            prompt_text = _build_prompt(schema_prompt, content_chunk, legal_hint, values_only=VALUES_ONLY_MODE, is_native_pdf=bool(gcs_uri))
            
            if llm_provider == "vertex" and gcs_uri:
                msg_content = [
                    {"type": "text", "text": prompt_text},
                    {"type": "media", "file_uri": gcs_uri, "mime_type": "application/pdf"}
                ]
                messages = [HumanMessage(content=msg_content)]
                print(f"🚀 [AGENT] Usando Vertex AI con PDF Nativo: {gcs_uri}")
            else:
                messages = [HumanMessage(content=prompt_text)]

            response = llm.invoke(messages)
            raw = response.content if hasattr(response, "content") else str(response)
            print(f"🤖 [AGENT] Respuesta LLM ({llm_provider}/{llm_model}) primeros 400 chars:\n{raw[:400]}")
            return parse_llm_json(raw)

        if len(markdown_content) <= CHUNK_LIMIT:
            return {"fields": _invoke_llm(markdown_content)}
        else:
            print(f"📦 [AGENT] Documento largo ({len(markdown_content)} chars). Chunking activado…")
            chunks = [markdown_content[i:i + CHUNK_LIMIT] for i in range(0, len(markdown_content), CHUNK_LIMIT - OVERLAP)]
            full_data: dict = {}
            for idx, chunk in enumerate(chunks):
                print(f"🧠 [AGENT] Fragmento {idx+1}/{len(chunks)} ({len(chunk)} chars)…")
                try:
                    chunk_data = _invoke_llm(chunk)
                    for k, v in chunk_data.items():
                        if v and not full_data.get(k):
                            full_data[k] = v
                except Exception as ce:
                    print(f"⚠️ [AGENT] Error fragmento {idx+1}: {ce}")
            return {"fields": full_data}

    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "quota" in error_msg.lower() or "rate_limit" in error_msg.lower():
            print(f"⚠️ [QUOTA] Rate limit alcanzado, esperando 60s")
            time.sleep(60)
            return extract_form_data(markdown_content, json_schema, visual_analysis, image_paths, llm_provider, llm_model, act_id, gcs_uri)
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
    
    if isinstance(parsed, dict) and ("containers" in parsed or "controls" in parsed):
        return parsed
    return _flatten_llm_response(parsed)

def _flatten_llm_response(data) -> dict:
    flat: dict = {}
    UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

    def _extract(node):
        if isinstance(node, dict):
            u = node.get("uuid")
            v = node.get("value")
            if u and v is not None and v != "" and v != "null":
                flat[str(u)] = v
            if "controls" in node and isinstance(node["controls"], list):
                for ctrl in node["controls"]: _extract(ctrl)
            if "containers" in node and isinstance(node["containers"], list):
                for container in node["containers"]: _extract(container)
            for k, val in node.items():
                if k not in ("uuid", "value", "label", "controls", "containers", "repetitiva", "orden"):
                    if UUID_RE.match(str(k)) and val is not None and val != "" and val != "null":
                        flat[k] = val
                    elif isinstance(val, (dict, list)): _extract(val)
        elif isinstance(node, list):
            for item in node: _extract(item)

    _extract(data)
    if not flat and isinstance(data, dict):
        return {k: v for k, v in data.items() if v is not None and v != ""}
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
    controls = schema.get("controls")
    if controls is not None:
        m["controls"] = [minify_schema(c) for c in (controls if isinstance(controls, list) else []) if isinstance(c, dict) and c.get("uuid")]
    containers = schema.get("containers")
    if containers is not None:
        m["containers"] = [minify_schema(c) for c in (containers if isinstance(containers, list) else [])]
    return m if m else schema
