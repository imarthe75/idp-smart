# app/engine/ensemble.py
# Módulo para soporte de Ensemble: Granite + Qwen

import os
import json
import asyncio
from typing import Dict, Optional, Any
from core.config import settings
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import PromptTemplate
import httpx


class EnsembleLLM:
    """
    Combinador inteligente de dos modelos LLM.
    Soporta estrategias: sequential, parallel, adaptive
    """
    
    def __init__(self, primary_llm, secondary_llm, strategy: str = "sequential"):
        self.primary = primary_llm
        self.secondary = secondary_llm
        self.strategy = strategy
        
    def invoke(self, prompt_dict: Dict[str, str]) -> Dict:
        """
        Ejecuta strategy correspondiente.
        
        Args:
            prompt_dict: {"document_md": str, "schema": dict, ...}
        
        Returns:
            dict: JSON extraído mejorado
        """
        if self.strategy == "sequential":
            return self._sequential(prompt_dict)
        elif self.strategy == "parallel":
            return self._parallel_sync(prompt_dict)
        elif self.strategy == "adaptive":
            return self._adaptive(prompt_dict)
        else:
            return self._sequential(prompt_dict)
    
    def _sequential(self, prompt_dict: Dict[str, str]) -> Dict:
        """
        1. Modelo principal (Granite) extrae
        2. Modelo secundario (Qwen) refina
        """
        print("[ENSEMBLE] Estrategia SECUENCIAL activada")
        
        # Paso 1: Granite extrae
        primary_response = self.primary.invoke(prompt_dict)
        primary_json = self._parse_response(primary_response)
        
        print(f"[GRANITE] Extracción primaria: {len(primary_json)} campos")
        
        # Paso 2: Qwen refina
        refine_prompt = self._build_refine_prompt(
            primary_json,
            prompt_dict["document_md"],
            prompt_dict.get("schema", {})
        )
        
        secondary_response = self.secondary.invoke({"text": refine_prompt})
        refined_json = self._parse_response(secondary_response)
        
        print(f"[QWEN] Refinamiento: {len(refined_json)} campos finales")
        
        # Paso 3: Merge inteligente
        result = self._smart_merge(primary_json, refined_json)
        return result
    
    def _parallel_sync(self, prompt_dict: Dict[str, str]) -> Dict:
        """
        Ambos modelos ejecutan en paralelo (simulado = threads)
        """
        print("[ENSEMBLE] Estrategia PARALELA activada")
        
        import threading
        results = {}
        
        def run_primary():
            response = self.primary.invoke(prompt_dict)
            results["primary"] = self._parse_response(response)
        
        def run_secondary():
            response = self.secondary.invoke(prompt_dict)
            results["secondary"] = self._parse_response(response)
        
        t1 = threading.Thread(target=run_primary)
        t2 = threading.Thread(target=run_secondary)
        
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        
        print(f"[GRANITE] {len(results['primary'])} campos")
        print(f"[QWEN] {len(results['secondary'])} campos")
        
        # Merge por votación
        return self._voting_merge(results["primary"], results["secondary"])
    
    def _adaptive(self, prompt_dict: Dict[str, str]) -> Dict:
        """
        1. Granite extrae con confidence scores
        2. si confidence < threshold → Qwen refina
        3. Merge adaptivo
        """
        print("[ENSEMBLE] Estrategia ADAPTIVA activada")
        
        primary_response = self.primary.invoke(prompt_dict)
        primary_json = self._parse_response(primary_response)
        
        # Calcular "confianza" (heurística simple)
        confidence = self._estimate_confidence(primary_json, prompt_dict["document_md"])
        
        print(f"[GRANITE] Confianza: {confidence:.0%}")
        
        if confidence < settings.ensemble_confidence_threshold:
            print(f"[QWEN] Confianza baja ({confidence:.0%} < {settings.ensemble_confidence_threshold:.0%}), pidiendo segunda opinión...")
            
            refine_prompt = self._build_refine_prompt(
                primary_json,
                prompt_dict["document_md"],
                prompt_dict.get("schema", {})
            )
            
            secondary_response = self.secondary.invoke({"text": refine_prompt})
            refined_json = self._parse_response(secondary_response)
            
            return self._smart_merge(primary_json, refined_json)
        else:
            print(f"[OK] Confianza suficiente, retornando resultado de Granite")
            return primary_json
    
    # ─── Helpers ──────────────────────────────────────────
    
    def _parse_response(self, response):
        """Extrae JSON de respuesta LLM"""
        try:
            if hasattr(response, 'content'):
                text_content = response.content
            else:
                text_content = str(response)
            
            # Buscar JSON en la respuesta de forma más flexible
            import re
            # Primero intentar bloques markdown triple backticks
            md_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text_content, re.DOTALL)
            if md_match:
                return json.loads(md_match.group(1))
            
            # Luego intentar cualquier bloque que empiece con { y termine con }
            json_match = re.search(r'(\{.*\})', text_content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            
            return {}
        except Exception as e:
            print(f"⚠️ Error parseando respuesta: {e}")
            return {}
    
    def _smart_merge(self, primary: Dict, secondary: Dict) -> Dict:
        """
        Merge inteligente: 
        - Conserva datos de Granite (visión)
        - Complementa con Qwen (razonamiento)
        """
        result = primary.copy()
        
        for key, val in secondary.items():
            if key not in result or result[key] is None or result[key] == "":
                # Qwen completa vacíos de Granite
                result[key] = val
            elif isinstance(val, dict) and isinstance(result.get(key), dict):
                # Si es dict, merge recursivo
                result[key] = self._smart_merge(result[key], val)
        
        return result
    
    def _voting_merge(self, primary: Dict, secondary: Dict) -> Dict:
        """
        Merge por votación:
        - Si coinciden: usar valor
        - Si no coinciden: priorizar Granite (visión > razonamiento)
        """
        result = {}
        all_keys = set(list(primary.keys()) + list(secondary.keys()))
        
        for key in all_keys:
            v1 = primary.get(key)
            v2 = secondary.get(key)
            
            if v1 == v2:
                # Coinciden = alta confianza
                result[key] = v1
            elif v1 is not None and v2 is None:
                # Solo en Granite
                result[key] = v1
            elif v1 is None and v2 is not None:
                # Solo en Qwen
                result[key] = v2
            else:
                # Conflicto: Granite gana (es especialista en visión)
                result[key] = v1
        
        return result
    
    def _estimate_confidence(self, extracted: Dict, document: str) -> float:
        """
        Estima confianza de extracción (heurística).
        
        Indicadores:
        - Cantidad de campos extraídos vs esperados
        - Cobertura del documento
        - Campos vacíos
        """
        if not extracted:
            return 0.0
        
        # Métrica simple: % de campos no-vacíos
        total_fields = len(extracted)
        non_empty = sum(1 for v in extracted.values() if v is not None and v != "")
        
        coverage = non_empty / total_fields if total_fields > 0 else 0.0
        
        # Bonus si el documento es largo (más contexto)
        doc_length = len(document)
        if doc_length > 5000:
            coverage = min(1.0, coverage + 0.1)
        elif doc_length < 1000:
            coverage = max(0.0, coverage - 0.1)
        
        return coverage
    
    def _build_refine_prompt(self, extracted: Dict, markdown: str, schema: Dict) -> str:
        """
        Construye prompt para que Qwen refine extracción de Granite.
        """
        return f"""
REFINAMIENTO DE EXTRACCIÓN DE DATOS LEGALES

Documento MD (ya parcialmente extraído):
─────────────────────────────────────────
{markdown[:2000]}  # Primeros 2000 caracteres

Datos ya extraídos por modelo de visión:
─────────────────────────────────────────
{json.dumps(extracted, ensure_ascii=False, indent=2)}

Tu tarea:
1. Analiza nuevamente el documento
2. Valida que los datos extraídos sean correctos
3. Completa campos faltantes si es posible
4. Resuelve cualquier inconsistencia
5. Retorna JSON mejorado SOLO (sin explicaciones)

Schema esperado:
{json.dumps(schema, ensure_ascii=False, indent=2)[:1000]}

Respuesta (JSON válido únicamente):
"""


def get_ensemble_llm(use_ensemble: bool = None) -> Any:
    """
    Factory para obtener LLM principal ± ensemble.
    
    Returns:
        EnsembleLLM o LLM simple según configuración
    """
    use_ens = use_ensemble or settings.use_ensemble
    
    # Modelo principal (Granite)
    primary_llm = ChatOpenAI(
        base_url=settings.localai_url,
        api_key="not-needed",
        model=settings.model_reasoning,
        temperature=settings.localai_temperature,
        max_tokens=settings.localai_max_tokens,
        timeout=settings.localai_timeout,
        model_kwargs={"response_format": {"type": "json_object"}},
        verbose=True
    )
    
    if not use_ens:
        return primary_llm
    
    # Cargar modelo secundario (Qwen)
    print(f"[ENSEMBLE] Cargando modelo secundario: {settings.ensemble_provider}")
    
    if settings.ensemble_provider == "localai":
        secondary_llm = ChatOpenAI(
            base_url=settings.qwen_base_url,
            api_key="not-needed",
            model=settings.qwen_model,
            temperature=settings.qwen_temperature,
            max_tokens=settings.localai_max_tokens,
            timeout=settings.localai_timeout,
            model_kwargs={"response_format": {"type": "json_object"}},
            verbose=False
        )
    
    elif settings.ensemble_provider == "runpod":
        secondary_llm = RunPodLLMWrapper(
            endpoint_id=settings.qwen_runpod_endpoint,
            api_key=settings.qwen_runpod_api_key
        )
    
    elif settings.ensemble_provider == "google":
        secondary_llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0.3
        )
    
    else:
        print(f"⚠️ ensemble_provider desconocido: {settings.ensemble_provider}")
        return primary_llm
    
    # Retornar ensemble
    return EnsembleLLM(
        primary_llm,
        secondary_llm,
        strategy=settings.ensemble_strategy
    )


class RunPodLLMWrapper:
    """
    Wrapper para llamar Qwen en RunPod Serverless.
    Convierte a interfaz compatible con LangChain.
    """
    
    def __init__(self, endpoint_id: str, api_key: str):
        self.endpoint_id = endpoint_id
        self.api_key = api_key
        self.base_url = f"https://api.runpod.io/v2/{endpoint_id}"
    
    def invoke(self, prompt_dict) -> str:
        """Llama a RunPod y espera resultado"""
        
        if isinstance(prompt_dict, dict):
            text = prompt_dict.get("text") or prompt_dict.get("document_md", "")
        else:
            text = str(prompt_dict)
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "input": {
                "prompt": text,
                "max_tokens": 2048,
                "temperature": 0.3
            }
        }
        
        try:
            response = httpx.post(
                f"{self.base_url}/run",
                json=payload,
                headers=headers,
                timeout=300.0
            )
            
            result = response.json()
            
            # RunPod es async, polling si es necesario
            if result.get("status") == "IN_RUN":
                request_id = result["id"]
                return self._poll_result(request_id, headers)
            
            return result.get("output", {}).get("text", "")
        
        except Exception as e:
            print(f"❌ Error llamando RunPod: {e}")
            return ""
    
    def _poll_result(self, request_id: str, headers: Dict, max_attempts: int = 60):
        """Poll RunPod hasta obtener resultado"""
        import time
        
        for attempt in range(max_attempts):
            try:
                response = httpx.get(
                    f"{self.base_url}/{request_id}",
                    headers=headers,
                    timeout=30.0
                )
                
                result = response.json()
                
                if result.get("status") == "COMPLETED":
                    return result.get("output", {}).get("text", "")
                elif result.get("status") == "FAILED":
                    print(f"❌ RunPod falló: {result.get('error')}")
                    return ""
                
                # Esperar 1 segundo antes de reintentar
                time.sleep(1)
                print(f"  [RunPod] Esperando... ({attempt + 1}/{max_attempts})")
            
            except Exception as e:
                print(f"⚠️ Error en polling: {e}")
                time.sleep(1)
        
        print(f"❌ Timeout esperando RunPod")
        return ""
