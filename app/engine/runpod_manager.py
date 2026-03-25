"""
RunPod Manager — idp-smart
----------------------------
Gestión del ciclo de vida de Pods de RunPod (encender / apagar).

Controla los pods de:
  - Docling GPU (OCR/Vision)
  - LLM (VLLM: Granite / Qwen)

Variables requeridas en .env:
  RUNPOD_ENABLED=true
  RUNPOD_API_KEY=...
  RUNPOD_POD_DOCLING_ID=...   (ID del pod de Docling)
  RUNPOD_POD_LLM_ID=...       (ID del pod de LLM)
  RUNPOD_IDLE_TIMEOUT=300     (segundos de inactividad antes de apagar)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Estado compartido (en memoria; persiste mientras el worker vive)
_pod_last_used: dict[str, float] = {}
_pod_status: dict[str, str] = {}         # "running" | "stopped" | "unknown"
_lock = threading.Lock()


def _runpod_api_call(method: str, path: str, body: dict | None = None) -> dict:
    """Llama a la API REST de RunPod con reintentos."""
    import requests

    api_key = os.environ.get("RUNPOD_API_KEY", "")
    base = "https://rest.runpod.io/v1"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    for attempt in range(3):
        try:
            resp = requests.request(
                method,
                f"{base}{path}",
                headers=headers,
                json=body,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("RunPod API error (intento %d): %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return {}


def get_pod_status(pod_id: str) -> str:
    """Retorna el estado actual del pod: 'RUNNING' | 'EXITED' | 'UNKNOWN'."""
    data = _runpod_api_call("GET", f"/pods/{pod_id}")
    return data.get("desiredStatus", "UNKNOWN").upper()


def start_pod(pod_id: str) -> bool:
    """Enciende el pod y espera a que esté listo (máx 3 min)."""
    logger.info("Encendiendo pod RunPod: %s", pod_id)
    _runpod_api_call("POST", f"/pods/{pod_id}/start")

    # Esperar a que el pod esté en RUNNING
    for _ in range(36):  # 36 × 5s = 3 min
        time.sleep(5)
        status = get_pod_status(pod_id)
        if status == "RUNNING":
            with _lock:
                _pod_status[pod_id] = "running"
                _pod_last_used[pod_id] = time.time()
            logger.info("Pod %s encendido exitosamente.", pod_id)
            return True

    logger.error("Timeout esperando pod %s.", pod_id)
    return False


def stop_pod(pod_id: str) -> bool:
    """Apaga el pod para reducir costos."""
    logger.info("Apagando pod RunPod: %s", pod_id)
    _runpod_api_call("POST", f"/pods/{pod_id}/stop")
    with _lock:
        _pod_status[pod_id] = "stopped"
    return True


def ensure_pod_running(pod_id: str) -> bool:
    """
    Garantiza que el pod esté encendido antes de enviar trabajo.
    Si está apagado, lo enciende y espera.
    """
    with _lock:
        status = _pod_status.get(pod_id, "unknown")
        _pod_last_used[pod_id] = time.time()

    if status == "running":
        return True

    # Verificar en API antes de encender
    current = get_pod_status(pod_id)
    if current == "RUNNING":
        with _lock:
            _pod_status[pod_id] = "running"
        return True

    return start_pod(pod_id)


def touch_pod(pod_id: str) -> None:
    """Actualiza el timestamp de último uso para el idle watcher."""
    with _lock:
        _pod_last_used[pod_id] = time.time()


def start_idle_watcher(pod_ids: list[str], idle_timeout: int = 300) -> None:
    """
    Lanza un hilo daemon que apaga los pods tras `idle_timeout`
    segundos de inactividad.
    """
    def _watch():
        while True:
            time.sleep(60)
            now = time.time()
            with _lock:
                items = list(_pod_last_used.items())
            for pid, last in items:
                if pid in pod_ids:
                    idle_secs = now - last
                    if idle_secs > idle_timeout:
                        logger.info(
                            "Pod %s inactivo por %.0fs → apagando.", pid, idle_secs
                        )
                        stop_pod(pid)

    t = threading.Thread(target=_watch, daemon=True, name="runpod-idle-watcher")
    t.start()
    logger.info(
        "RunPod idle watcher iniciado (timeout=%ds, pods=%s)", idle_timeout, pod_ids
    )


def get_pod_url(pod_id: str, port: int = 8000) -> Optional[str]:
    """
    Retorna la URL pública del pod (host:port) para enviar peticiones.
    RunPod expone los servicios como proxy.
    """
    data = _runpod_api_call("GET", f"/pods/{pod_id}")
    runtime = data.get("runtime", {})
    ports = runtime.get("ports", [])
    for p in ports:
        if p.get("privatePort") == port:
            return f"https://{pod_id}-{port}.proxy.runpod.net/v1"
    return None
