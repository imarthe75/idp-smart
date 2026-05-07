"""
Smart Router — idp-smart
--------------------------
Función get_best_worker() que decide si un documento debe procesarse
localmente (VLLM / RunPod) o derivarse a la nube (Gemini / Claude / OpenAI).

Criterios de decisión:
  1. Cola de Celery: si hay más tareas pendientes que MAX_LOCAL_QUEUE → cloud
  2. Proveedor forzado por env (LLM_PROVIDER)
  3. Disponibilidad de GPU (hardware_detector)

El router también registra la decisión en los eventos de la tarea para
que quede trazabilidad completa del costo.
"""
from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class WorkerDestination(str, Enum):
    LOCAL_VLLM   = "vllm"      # Self-hosted VLLM (Dell / RunPod)
    CLOUD_GOOGLE = "google"    # Google Gemini
    CLOUD_CLAUDE = "anthropic" # Anthropic Claude
    CLOUD_OPENAI = "openai"    # OpenAI GPT-4o


def _get_celery_queue_depth() -> int:
    """
    Inspecciona la cola 'celery' en Valkey/Redis y retorna el número
    de tareas pendientes. Retorna 0 si no se puede conectar.
    """
    try:
        import redis

        url = os.environ.get("VALKEY_URL", "redis://localhost:6379/0")
        r = redis.from_url(url, socket_connect_timeout=2)
        return r.llen("celery")
    except Exception as exc:
        logger.debug("No se pudo leer la cola Celery: %s", exc)
        return 0


def get_best_worker(
    *,
    force_provider: Optional[str] = None,
    log_fn=None,
) -> WorkerDestination:
    """
    Determina el mejor motor de inferencia para la tarea actual.

    Args:
        force_provider: Si se especifica, ignora la lógica de routing y
                        usa ese proveedor directamente (para pruebas).
        log_fn:         Función de logging opcional para registrar la decisión.
                        Firma: log_fn(msg: str, level: str = "INFO")

    Returns:
        WorkerDestination con la decisión de routing.
    """
    def _log(msg: str, level: str = "INFO"):
        getattr(logger, level.lower())(msg)
        if log_fn:
            log_fn(msg, level)

    # 1. Forzado por parámetro explícito o variable de entorno
    provider_env = (force_provider or os.environ.get("LLM_PROVIDER", "google")).lower()

    # Mapeo de valores de LLM_PROVIDER a WorkerDestination
    _MAP = {
        "vllm":      WorkerDestination.LOCAL_VLLM,
        "local":     WorkerDestination.LOCAL_VLLM,   # alias
        "runpod":    WorkerDestination.LOCAL_VLLM,   # alias
        "google":    WorkerDestination.CLOUD_GOOGLE,
        "gemini":    WorkerDestination.CLOUD_GOOGLE, # alias
        "anthropic": WorkerDestination.CLOUD_CLAUDE,
        "claude":    WorkerDestination.CLOUD_CLAUDE, # alias
        "openai":    WorkerDestination.CLOUD_OPENAI,
        "gpt":       WorkerDestination.CLOUD_OPENAI, # alias
    }

    if provider_env not in _MAP:
        _log(f"LLM_PROVIDER desconocido '{provider_env}', usando 'google'.", "WARNING")
        provider_env = "google"

    destination = _MAP[provider_env]

    # 2. Si es local, verificar si la cola está saturada y hay fallback habilitado
    if destination == WorkerDestination.LOCAL_VLLM:
        cloud_fallback = os.environ.get("ENABLE_CLOUD_FALLBACK", "false").lower() == "true"
        max_queue = int(os.environ.get("MAX_LOCAL_QUEUE", "5"))

        if cloud_fallback:
            queue_depth = _get_celery_queue_depth()
            if queue_depth > max_queue:
                fallback_provider = os.environ.get("CLOUD_FALLBACK_PROVIDER", "google")
                fallback_dest = _MAP.get(fallback_provider, WorkerDestination.CLOUD_GOOGLE)
                _log(
                    f"Cola saturada ({queue_depth} tareas > umbral {max_queue}). "
                    f"Derivando a cloud: {fallback_dest.value}",
                    "WARNING",
                )
                return fallback_dest

    # 3. Si es local sin GPU, advertir (no bloquear — puede haber RunPod configurado)
    if destination == WorkerDestination.LOCAL_VLLM:
        try:
            from engine.hardware_detector import detect_hardware
            hw = detect_hardware()
            if not hw.has_gpu:
                _log(
                    "Sin GPU detectada. LLM_PROVIDER=vllm requiere VRAM. "
                    "Considera usar LLM_PROVIDER=google para CPU-only.",
                    "WARNING",
                )
        except Exception:
            pass

    _log(f"Smart Router → {destination.value.upper()}")
    return destination


def is_cloud_provider(destination: WorkerDestination) -> bool:
    return destination in (
        WorkerDestination.CLOUD_GOOGLE,
        WorkerDestination.CLOUD_CLAUDE,
        WorkerDestination.CLOUD_OPENAI,
    )
