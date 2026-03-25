"""
LLM Provider Factory — idp-smart
-----------------------------------
Abstracción agnóstica al proveedor de razonamiento.
Controlado por la variable de entorno LLM_PROVIDER:

  MOTOR LOCAL / SELF-HOSTED:
    vllm      → VLLM local (Dell L40S / RunPod Pod) con Granite/Qwen
                Requiere: LOCAL_API_URL  (ej: http://localhost:8000)
                          LOCAL_LLM_MODEL (ej: granite-3.0-8b-instruct)

  MOTOR CLOUD (External APIs):
    google    → Google Gemini 1.5 Pro/Flash
                Requiere: GOOGLE_API_KEY
    anthropic → Anthropic Claude 3.5 Sonnet
                Requiere: ANTHROPIC_API_KEY
    openai    → OpenAI GPT-4o / GPT-4o-mini
                Requiere: OPENAI_API_KEY

Aliases aceptados:
    local, runpod → vllm
    gemini        → google
    claude        → anthropic
    gpt           → openai

Todos los proveedores implementan:
  - invoke(prompt, system) -> str
  - invoke_with_cost(prompt, system) -> (str, float)   [costo en USD]
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# ===========================================================================
# Clase BASE
# ===========================================================================
class BaseLLM:
    provider_name: str = "base"

    def invoke(self, prompt: str, system: str = "") -> str:
        raise NotImplementedError

    def invoke_with_cost(self, prompt: str, system: str = "") -> Tuple[str, float]:
        return self.invoke(prompt, system), 0.0


# ===========================================================================
# VLLM — Self-Hosted (Dell L40S / RunPod Pod)
# ===========================================================================
class VLLMProvider(BaseLLM):
    """
    Cliente OpenAI-compatible para servidores VLLM.
    Funciona con cualquier servidor que exponga /v1/chat/completions:
      - VLLM local en Dell (puerto 8000/8001)
      - RunPod Pods con la imagen vllm/vllm-openai

    Variables:
      LOCAL_API_URL   → URL base (ej: http://10.4.3.23:8000)
      LOCAL_LLM_MODEL → Nombre del modelo cargado
      LOCAL_LLM_TIMEOUT → Timeout en segundos (default: 300)

    Si RUNPOD_ENABLED=true, activa el pod automáticamente antes de la llamada.
    """
    provider_name = "vllm"

    def __init__(self):
        self._base_url = os.environ.get(
            "LOCAL_API_URL",
            os.environ.get("RUNPOD_LLM_URL", "http://localhost:8000")
        ).rstrip("/")
        self._model = os.environ.get("LOCAL_LLM_MODEL", "granite-3.0-8b-instruct")
        self._timeout = int(os.environ.get("LOCAL_LLM_TIMEOUT", "300"))
        self._runpod_enabled = os.environ.get("RUNPOD_ENABLED", "false").lower() == "true"
        self._pod_id = os.environ.get("RUNPOD_POD_LLM_ID", "")

        logger.info(
            "VLLMProvider: url=%s model=%s runpod=%s",
            self._base_url,
            self._model,
            self._runpod_enabled,
        )

    def _ensure_pod(self):
        if self._runpod_enabled and self._pod_id:
            from engine.runpod_manager import ensure_pod_running, touch_pod
            ensure_pod_running(self._pod_id)
            # Actualizar URL dinámica del pod
            from engine.runpod_manager import get_pod_url
            url = get_pod_url(self._pod_id, port=8000)
            if url:
                self._base_url = url.rstrip("/v1").rstrip("/")
            touch_pod(self._pod_id)

    def invoke(self, prompt: str, system: str = "") -> str:
        import requests

        self._ensure_pod()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 4096,
        }
        resp = requests.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ===========================================================================
# GOOGLE GEMINI (cloud)
# ===========================================================================
class GeminiProvider(BaseLLM):
    """
    Cliente para Google Gemini 1.5 Pro / Flash.
    Variables: GOOGLE_API_KEY, GEMINI_MODEL
    """
    provider_name = "google"

    _PRICE_INPUT  = 0.075  / 1_000_000   # $/token input  (Flash)
    _PRICE_OUTPUT = 0.30   / 1_000_000   # $/token output (Flash)

    def __init__(self):
        import google.generativeai as genai

        api_key = os.environ["GOOGLE_API_KEY"]
        genai.configure(api_key=api_key)
        self._model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self._model = genai.GenerativeModel(self._model_name)
        logger.info("GeminiProvider: model=%s", self._model_name)

    def invoke(self, prompt: str, system: str = "") -> str:
        full = f"{system}\n\n{prompt}" if system else prompt
        return self._model.generate_content(full).text

    def invoke_with_cost(self, prompt: str, system: str = "") -> Tuple[str, float]:
        full = f"{system}\n\n{prompt}" if system else prompt
        response = self._model.generate_content(full)
        usage = response.usage_metadata
        cost = (
            usage.prompt_token_count     * self._PRICE_INPUT
            + usage.candidates_token_count * self._PRICE_OUTPUT
        )
        logger.info("Gemini → $%.6f USD (%s tokens)", cost, usage)
        return response.text, cost


# ===========================================================================
# ANTHROPIC CLAUDE (cloud)
# ===========================================================================
class AnthropicProvider(BaseLLM):
    """
    Cliente para Anthropic Claude 3.5 Sonnet.
    Variables: ANTHROPIC_API_KEY, CLAUDE_MODEL
    """
    provider_name = "anthropic"

    _PRICE_INPUT  = 3.00  / 1_000_000
    _PRICE_OUTPUT = 15.00 / 1_000_000

    def __init__(self):
        import anthropic

        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._model = os.environ.get("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
        logger.info("AnthropicProvider: model=%s", self._model)

    def invoke(self, prompt: str, system: str = "") -> str:
        kwargs = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        return self._client.messages.create(**kwargs).content[0].text

    def invoke_with_cost(self, prompt: str, system: str = "") -> Tuple[str, float]:
        kwargs = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        msg = self._client.messages.create(**kwargs)
        cost = (
            msg.usage.input_tokens  * self._PRICE_INPUT
            + msg.usage.output_tokens * self._PRICE_OUTPUT
        )
        logger.info("Claude → $%.6f USD (%s tokens)", cost, msg.usage)
        return msg.content[0].text, cost


# ===========================================================================
# OPENAI GPT-4o / GPT-4o-mini (cloud)
# ===========================================================================
class OpenAIProvider(BaseLLM):
    """
    Cliente para OpenAI GPT-4o / GPT-4o-mini.
    Variables: OPENAI_API_KEY, OPENAI_MODEL
    """
    provider_name = "openai"

    _PRICES = {
        "gpt-4o":      (5.00  / 1_000_000, 15.00 / 1_000_000),
        "gpt-4o-mini": (0.15  / 1_000_000, 0.60  / 1_000_000),
    }

    def __init__(self):
        from openai import OpenAI

        self._client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self._model  = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self._pi, self._po = self._PRICES.get(
            self._model, (0.15 / 1_000_000, 0.60 / 1_000_000)
        )
        logger.info("OpenAIProvider: model=%s", self._model)

    def invoke(self, prompt: str, system: str = "") -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        resp = self._client.chat.completions.create(
            model=self._model, messages=msgs, temperature=0.0, max_tokens=4096
        )
        return resp.choices[0].message.content

    def invoke_with_cost(self, prompt: str, system: str = "") -> Tuple[str, float]:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        resp = self._client.chat.completions.create(
            model=self._model, messages=msgs, temperature=0.0, max_tokens=4096
        )
        cost = (
            resp.usage.prompt_tokens     * self._pi
            + resp.usage.completion_tokens * self._po
        )
        logger.info("OpenAI %s → $%.6f USD (%s tokens)", self._model, cost, resp.usage)
        return resp.choices[0].message.content, cost


# ===========================================================================
# FACTORY
# ===========================================================================
# Aliases → clase canónica
_ALIAS_MAP: dict[str, type[BaseLLM]] = {
    # Local / self-hosted
    "vllm":      VLLMProvider,
    "local":     VLLMProvider,
    "runpod":    VLLMProvider,
    # Cloud
    "google":    GeminiProvider,
    "gemini":    GeminiProvider,
    "anthropic": AnthropicProvider,
    "claude":    AnthropicProvider,
    "openai":    OpenAIProvider,
    "gpt":       OpenAIProvider,
}


def get_llm_provider(
    provider_name: Optional[str] = None,
    fallback_to_cloud: bool = True,
) -> BaseLLM:
    """
    Retorna una instancia del proveedor LLM configurado.

    Args:
        provider_name:    Nombre del proveedor (sobreescribe LLM_PROVIDER).
        fallback_to_cloud: Si True y el proveedor local falla, cae al
                           proveedor de CLOUD_FALLBACK_PROVIDER.
    """
    name = (provider_name or os.environ.get("LLM_PROVIDER", "google")).lower()
    provider_cls = _ALIAS_MAP.get(name)

    if provider_cls is None:
        raise ValueError(
            f"Proveedor LLM desconocido: '{name}'. "
            f"Opciones: {sorted(set(_ALIAS_MAP.keys()))}"
        )

    logger.info("Inicializando proveedor LLM: %s → %s", name, provider_cls.__name__)
    try:
        return provider_cls()
    except Exception as exc:
        logger.error("Error inicializando LLM '%s': %s", name, exc)
        is_local = provider_cls is VLLMProvider
        if fallback_to_cloud and is_local:
            fallback = os.environ.get("CLOUD_FALLBACK_PROVIDER", "google")
            logger.warning("Activando fallback cloud: %s", fallback)
            return _ALIAS_MAP.get(fallback, GeminiProvider)()
        raise
