"""
idp-smart — Módulo de Logging de Ejecuciones
=============================================
Registra cada etapa del proceso de extracción en la tabla idp_smart.process_logs.
Provee un helper síncrono (para Celery) y uno asíncrono (para FastAPI).
"""
import time
import logging
from contextlib import contextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

# Logger estándar de Python (también escribe en stdout/stderr de Docker)
logger = logging.getLogger("idp_smart")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ---------------------------------------------------------------------------
# Helpers síncronos — usados por Celery
# ---------------------------------------------------------------------------

def log_event(
    engine: Engine,
    task_id: str,
    stage: str,
    message: str,
    level: str = "INFO",
    detail: dict | None = None,
    duration_ms: float | None = None,
) -> None:
    """
    Inserta un registro en process_logs usando el engine síncrono de SQLAlchemy
    (modo Celery). También emite el mensaje al logger estándar de Python.
    """
    log_fn = {
        "DEBUG":   logger.debug,
        "INFO":    logger.info,
        "WARNING": logger.warning,
        "ERROR":   logger.error,
    }.get(level, logger.info)

    log_fn("[%s] [%s] %s", task_id, stage, message)

    import json
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO idp_smart.process_logs
                        (task_id, stage, level, message, detail, duration_ms)
                    VALUES
                        (:task_id, :stage, :level, :message, :detail, :duration_ms)
                """),
                {
                    "task_id":     task_id,
                    "stage":       stage,
                    "level":       level,
                    "message":     message,
                    "detail":      json.dumps(detail) if detail else None,
                    "duration_ms": duration_ms,
                },
            )
    except Exception as exc:
        # El fallo de log no debe interrumpir el proceso principal
        logger.error("[%s] No se pudo escribir log en BD: %s", task_id, exc)


@contextmanager
def timed_stage(engine: Engine, task_id: str, stage: str, message: str, detail: dict | None = None):
    """
    Context manager que mide el tiempo de una etapa y la registra automáticamente.

    Uso:
        with timed_stage(engine, task_id, "VISION", "Extrayendo Markdown con Docling"):
            markdown = extract_markdown(...)
    """
    t0 = time.monotonic()
    log_event(engine, task_id, stage, f"INICIO — {message}", detail=detail)
    try:
        yield
        duration = (time.monotonic() - t0) * 1000
        log_event(engine, task_id, stage, f"FIN — {message}", duration_ms=round(duration, 2))
    except Exception as exc:
        duration = (time.monotonic() - t0) * 1000
        log_event(
            engine, task_id, stage,
            f"ERROR — {message}: {exc}",
            level="ERROR",
            detail={"error": str(exc), "type": type(exc).__name__},
            duration_ms=round(duration, 2),
        )
        raise


# ---------------------------------------------------------------------------
# JSON Simplificado — extrae solo {label: value} del JSON del sistema Java
# ---------------------------------------------------------------------------

def build_simplified_json(full_json: dict) -> dict:
    """
    Recorre el JSON completo del sistema Java y extrae un diccionario
    plano con el formato { "label": "value" } para cada control visible.

    Garantiza que los VALUES sean siempre escalares o listas de dicts con
    claves de etiqueta (no UUIDs). Nunca debería aparecer un UUID como clave
    en el JSON simplificado final.

    Ejemplo de salida:
        {
          "Folio real electrónico": "12345678901234567890",
          "No. Notario": "42",
          "Nombre": "Juan Pérez García"
        }
    """
    simplified: dict[str, Any] = {}

    # ── Paso 1: construir índice uuid → label para poder resolver cualquier
    #            diccionario de UUIDs que el LLM haya devuelto mezclado ──────
    uuid_to_label: dict[str, str] = {}

    def _collect_uuid_labels(node):
        if isinstance(node, dict):
            uid = node.get("uuid")
            lbl = node.get("label")
            if uid and lbl:
                uuid_to_label[uid] = lbl.strip()
            for v in node.values():
                _collect_uuid_labels(v)
        elif isinstance(node, list):
            for item in node:
                _collect_uuid_labels(item)

    _collect_uuid_labels(full_json)

    # ── Paso 2: función auxiliar para convertir un valor a su forma legible ──
    def _resolve_value(val):
        """
        Si val es un dict de UUIDs (como devuelve el LLM a veces), lo convierte
        a un dict con etiquetas.  Si es una lista de tales dicts, la procesa
        elemento a elemento.  Los escalares se devuelven tal cual.
        """
        if isinstance(val, dict):
            resolved = {}
            for k, v in val.items():
                label = uuid_to_label.get(k, k)   # usa etiqueta si la hay
                if isinstance(v, (dict, list)):
                    resolved[label] = _resolve_value(v)
                elif v not in (None, "", []):
                    resolved[label] = v
            return resolved if resolved else None
        elif isinstance(val, list):
            result = []
            for item in val:
                r = _resolve_value(item)
                if r:
                    result.append(r)
            return result if result else None
        else:
            return val

    # ── Paso 3: recorrer el JSON completo y extraer label→value ──────────────
    def _recurse(node, prefix=""):
        if isinstance(node, dict):
            # Nodo de control con label y value explícito
            if "label" in node and "value" in node:
                label = node.get("label", "").strip()
                value = node.get("value")

                # Ignorar valores vacíos o None simples
                if label and value is not None and value != "" and value != {}:
                    display_label = f"{label} {prefix}".strip()

                    if isinstance(value, dict):
                        # El LLM puso un dict de UUIDs como value — resolvemos etiquetas
                        resolved = _resolve_value(value)
                        if resolved:
                            simplified[display_label] = resolved
                    elif isinstance(value, list):
                        resolved = _resolve_value(value)
                        if resolved:
                            simplified[display_label] = resolved
                    else:
                        simplified[display_label] = value

            # Soporte para MATRICES (instancias clonadas por el mapper)
            if "instances" in node and isinstance(node["instances"], list):
                for i, instance in enumerate(node["instances"]):
                    _recurse(instance, prefix=f"({i+1})")

            # Seguir recorriendo valores anidados estándar
            for k, val in node.items():
                if k not in ("instances", "value"):
                    _recurse(val, prefix=prefix)

        elif isinstance(node, list):
            for item in node:
                _recurse(item, prefix=prefix)

    _recurse(full_json)
    return simplified
