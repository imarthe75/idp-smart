#!/usr/bin/env python3
"""
welcome_agent.py — Script de Inicio del Agente idp-smart / Proyecto Tolucón
=============================================================================
Ejecutar al iniciar cualquier sesión de desarrollo agéntico.
Valida el acceso a la memoria del agente, herramientas y servicios críticos.

Uso:
    python welcome_agent.py
    python welcome_agent.py --full      # Incluye validación de servicios Docker
    python welcome_agent.py --quiet     # Solo muestra errores
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent
AGENT_DIR = PROJECT_ROOT / ".agent"
APP_DIR = PROJECT_ROOT / "app"

AGENT_MEMORY_FILES = {
    "CONTEXT.md": "Visión del Proyecto Tolucón y arquitectura",
    "AGENT.md": "Estándares de desarrollo y protocolo",
    "STATE.md": "Estado actual y deudas técnicas",
    "RULES.md": "Reglas globales — Hard Rules V8.0",
    "MAP.md": "Mapa de arquitectura técnica V8.0",
    "PERSONAS.md": "Personas del agente (Analista, Arquitecto, Auditor)",
    "MODEL_REGISTRY.md": "Registro de modelos evaluados",
    "NOTARY_STYLE_GUIDE.md": "Guía de estilo JSON para datos notariales",
}

AGENT_V8_PATHS = {
    "BRAIN/bootstrap.py": "Motor RAG interno (ChromaDB)",
    "BRAIN/vector_store": "Vector store persistente (ChromaDB)",
    "MCP/tools.py": "Herramientas MCP (Docker, SQL, MinIO)",
    "DECISIONS": "Directorio ADR (Architecture Decision Records)",
}

MCP_CONFIG = PROJECT_ROOT / ".mcp" / "server-config.json"

CRITICAL_APP_FILES = {
    "app/main.py": "API FastAPI principal",
    "app/engine/agent.py": "Motor de extracción IA",
    "app/engine/dspy_programs.py": "Módulo DSPy V8.0",
    "app/worker/celery_app.py": "Worker Celery",
    ".env": "Configuración de entorno",
    ".mcp/server-config.json": "Configuración MCP V8.0",
}

OPTIONAL_SERVICES = {
    "PostgreSQL": ("localhost", 5432),
    "MinIO/SeaweedFS": ("localhost", 8333),
    "Valkey (Redis)": ("localhost", 6379),
    "Docling Server": ("localhost", 18001),
}

# ANSI colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
BLUE = "\033[94m"


# ─────────────────────────────────────────────────────────────────────────────
# Tipos
# ─────────────────────────────────────────────────────────────────────────────

class CheckResult(NamedTuple):
    name: str
    ok: bool
    message: str
    detail: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de display
# ─────────────────────────────────────────────────────────────────────────────

def _icon(ok: bool) -> str:
    return f"{GREEN}✅{RESET}" if ok else f"{RED}❌{RESET}"


def _warn_icon() -> str:
    return f"{YELLOW}⚠️{RESET}"


def print_header() -> None:
    width = 66
    print(f"\n{BOLD}{CYAN}{'═' * width}{RESET}")
    print(f"{BOLD}{CYAN}  🏛️  idp-smart — Proyecto Tolucón{RESET}")
    print(f"{BOLD}{CYAN}  🤖  Sistema de Memoria Agéntica v1.0{RESET}")
    print(f"{DIM}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Python {sys.version.split()[0]}{RESET}")
    print(f"{BOLD}{CYAN}{'═' * width}{RESET}\n")


def print_section(title: str) -> None:
    print(f"\n{BOLD}{BLUE}▶ {title}{RESET}")
    print(f"{DIM}{'─' * 60}{RESET}")


def print_result(result: CheckResult, quiet: bool = False) -> None:
    if quiet and result.ok:
        return
    icon = _icon(result.ok)
    status = f"{icon} {result.name:<40} {result.message}"
    print(status)
    if result.detail:
        print(f"     {DIM}{result.detail}{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# Checks de Memoria
# ─────────────────────────────────────────────────────────────────────────────

def check_agent_memory() -> list[CheckResult]:
    """Verifica que todos los archivos de memoria del agente existen y tienen contenido."""
    results: list[CheckResult] = []

    if not AGENT_DIR.exists():
        results.append(CheckResult(
            ".agent/ dir", False,
            f"{RED}NO ENCONTRADO{RESET}",
            f"Esperado en: {AGENT_DIR}"
        ))
        return results

    for filename, description in AGENT_MEMORY_FILES.items():
        filepath = AGENT_DIR / filename
        if not filepath.exists():
            results.append(CheckResult(
                filename, False,
                f"{YELLOW}FALTANTE{RESET}",
                description
            ))
        else:
            size = filepath.stat().st_size
            if size < 100:
                results.append(CheckResult(
                    filename, False,
                    f"{YELLOW}VACÍO ({size} bytes){RESET}",
                    description
                ))
            else:
                mod_time = datetime.fromtimestamp(filepath.stat().st_mtime)
                results.append(CheckResult(
                    filename, True,
                    f"{GREEN}OK{RESET} ({size:,} bytes)",
                    f"{description} | Modificado: {mod_time.strftime('%Y-%m-%d %H:%M')}"
                ))

    return results


def check_state_freshness() -> CheckResult:
    """Verifica si STATE.md fue actualizado recientemente."""
    state_file = AGENT_DIR / "STATE.md"
    if not state_file.exists():
        return CheckResult("STATE.md freshness", False, "Archivo no encontrado")

    mod_time = datetime.fromtimestamp(state_file.stat().st_mtime)
    days_since = (datetime.now() - mod_time).days

    if days_since == 0:
        return CheckResult(
            "STATE.md freshness", True,
            f"{GREEN}Actualizado hoy{RESET}"
        )
    elif days_since <= 3:
        return CheckResult(
            "STATE.md freshness", True,
            f"{YELLOW}Actualizado hace {days_since} día(s){RESET}",
            "Considera actualizar STATE.md con el progreso reciente"
        )
    else:
        return CheckResult(
            "STATE.md freshness", False,
            f"{RED}Sin actualizar hace {days_since} día(s){RESET}",
            "⚠️  ACCIÓN REQUERIDA: Actualiza STATE.md antes de continuar"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Checks de Archivos del Proyecto
# ─────────────────────────────────────────────────────────────────────────────

def check_project_files() -> list[CheckResult]:
    """Verifica que los archivos críticos del proyecto existen."""
    results: list[CheckResult] = []

    for rel_path, description in CRITICAL_APP_FILES.items():
        filepath = PROJECT_ROOT / rel_path
        if not filepath.exists():
            results.append(CheckResult(
                rel_path, False,
                f"{RED}NO ENCONTRADO{RESET}",
                description
            ))
        else:
            size = filepath.stat().st_size
            results.append(CheckResult(
                rel_path, True,
                f"{GREEN}OK{RESET} ({size:,} bytes)",
                description
            ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Checks de Configuración (.env)
# ─────────────────────────────────────────────────────────────────────────────

def check_env_config() -> list[CheckResult]:
    """Verifica que las variables de entorno críticas están configuradas."""
    results: list[CheckResult] = []
    env_file = PROJECT_ROOT / ".env"

    REQUIRED_VARS = {
        "LLM_PROVIDER": "Proveedor LLM activo",
        "DB_HOST": "Host de PostgreSQL",
        "DB_NAME": "Nombre de la base de datos",
        "STORAGE_ENDPOINT": "Endpoint de MinIO/SeaweedFS",
        "VALKEY_URL": "URL de Valkey/Redis",
    }

    SENSITIVE_VARS = {
        "GOOGLE_API_KEY": "Google Gemini API",
        "GROQ_API_KEY": "Groq API (opcional)",
        "ANTHROPIC_API_KEY": "Anthropic Claude API (opcional)",
        "GCP_PROJECT_ID": "Google Cloud Project (para Vertex AI)",
    }

    # Cargar .env manualmente (sin python-dotenv como dependencia hard)
    env_values: dict[str, str] = {}
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env_values[key.strip()] = value.strip()

    # Combinar con variables de entorno del sistema
    for key in {**REQUIRED_VARS, **SENSITIVE_VARS}:
        env_values.setdefault(key, os.environ.get(key, ""))

    # Verificar requeridas
    for var, description in REQUIRED_VARS.items():
        value = env_values.get(var, "")
        if not value:
            results.append(CheckResult(
                var, False,
                f"{RED}NO CONFIGURADA{RESET}",
                description
            ))
        else:
            results.append(CheckResult(
                var, True,
                f"{GREEN}OK{RESET} = {value[:30]}{'...' if len(value) > 30 else ''}",
                description
            ))

    # Verificar sensibles (solo presencia, no valor)
    for var, description in SENSITIVE_VARS.items():
        value = env_values.get(var, "")
        if not value:
            results.append(CheckResult(
                var, False,
                f"{YELLOW}NO CONFIGURADA{RESET} (puede ser opcional)",
                description
            ))
        else:
            masked = f"{value[:6]}{'*' * (len(value) - 8)}{value[-2:]}" if len(value) > 8 else "***"
            results.append(CheckResult(
                var, True,
                f"{GREEN}OK{RESET} = {masked}",
                description
            ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Checks de Servicios (Opcional)
# ─────────────────────────────────────────────────────────────────────────────

def check_service(name: str, host: str, port: int, timeout: float = 1.0) -> CheckResult:
    """Intenta conexión TCP a un servicio."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return CheckResult(name, True, f"{GREEN}ONLINE{RESET} ({host}:{port})")
    except (ConnectionRefusedError, OSError, TimeoutError):
        return CheckResult(
            name, False,
            f"{YELLOW}OFFLINE{RESET} ({host}:{port})",
            "El servicio puede estar en Docker; esto es solo una advertencia"
        )


def check_optional_services() -> list[CheckResult]:
    """Verifica conectividad a servicios de infraestructura."""
    results: list[CheckResult] = []
    for name, (host, port) in OPTIONAL_SERVICES.items():
        results.append(check_service(name, host, port))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Verificación de Python y Dependencias
# ─────────────────────────────────────────────────────────────────────────────

def check_python_deps() -> list[CheckResult]:
    """Verifica que las dependencias críticas de Python están instaladas."""
    results: list[CheckResult] = []

    CRITICAL_DEPS = {
        "fastapi": "API Framework",
        "celery": "Task Queue",
        "sqlalchemy": "ORM / Base de Datos",
        "langchain_core": "LangChain Core",
        "langchain_google_genai": "Google Gemini LLM",
        "langchain_groq": "Groq LLM",
        "langchain_anthropic": "Anthropic Claude LLM",
        "langchain_ollama": "Ollama LLM (local)",
        "minio": "Cliente MinIO/SeaweedFS",
        "fitz": "PyMuPDF (PDF processing)",
    }

    OPTIONAL_DEPS = {
        "docling": "OCR Engine (Docling)",
        "langchain_google_vertexai": "Vertex AI LLM",
    }

    for package, description in CRITICAL_DEPS.items():
        try:
            __import__(package)
            results.append(CheckResult(
                package, True,
                f"{GREEN}INSTALADO{RESET}",
                description
            ))
        except ImportError:
            results.append(CheckResult(
                package, False,
                f"{RED}NO INSTALADO{RESET}",
                f"CRÍTICO: {description}"
            ))

    for package, description in OPTIONAL_DEPS.items():
        try:
            __import__(package)
            results.append(CheckResult(
                package, True,
                f"{GREEN}INSTALADO{RESET}",
                description
            ))
        except ImportError:
            results.append(CheckResult(
                package, False,
                f"{YELLOW}NO INSTALADO{RESET} (opcional)",
                description
            ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Resumen del Estado Actual (desde STATE.md)
# ─────────────────────────────────────────────────────────────────────────────

def print_state_summary() -> None:
    """Extrae y muestra un resumen del STATE.md."""
    state_file = AGENT_DIR / "STATE.md"
    if not state_file.exists():
        print(f"  {YELLOW}STATE.md no encontrado — no se puede mostrar resumen.{RESET}")
        return

    content = state_file.read_text(encoding="utf-8")
    lines = content.split("\n")

    # Extraer estado actual (primeras líneas después del encabezado)
    in_section = False
    printed = 0
    for line in lines:
        if "## 🟢 Estado Actual" in line:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.strip() and printed < 8:
            print(f"  {line}")
            printed += 1


# ─────────────────────────────────────────────────────────────────────────────
# Punto de Entrada Principal
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Script de bienvenida del agente idp-smart / Proyecto Tolucón"
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Incluir verificación de servicios Docker y dependencias Python"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Solo mostrar errores y advertencias"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Salida en formato JSON (para integración con otros scripts)"
    )
    args = parser.parse_args()

    all_results: list[CheckResult] = []
    errors = 0
    warnings = 0

    if not args.json:
        print_header()

    # ── 1. Memoria del Agente ──────────────────────────────────────────────
    if not args.quiet:
        print_section("1. Memoria del Agente (.agent/)")

    memory_results = check_agent_memory()
    freshness = check_state_freshness()
    memory_results.append(freshness)

    # V8.0 — verificar estructura BRAIN, MCP, DECISIONS
    for rel_path, description in AGENT_V8_PATHS.items():
        full_path = AGENT_DIR / rel_path
        exists = full_path.exists()
        if exists and full_path.is_dir():
            n_items = len(list(full_path.iterdir()))
            memory_results.append(CheckResult(
                f".agent/{rel_path}", True,
                f"{GREEN}OK{RESET} ({n_items} items)",
                description
            ))
        elif exists:
            size = full_path.stat().st_size
            memory_results.append(CheckResult(
                f".agent/{rel_path}", True,
                f"{GREEN}OK{RESET} ({size:,} bytes)",
                description
            ))
        else:
            memory_results.append(CheckResult(
                f".agent/{rel_path}", False,
                f"{YELLOW}NO ENCONTRADO{RESET}",
                description
            ))

    # Verificar MCP config
    if MCP_CONFIG.exists():
        memory_results.append(CheckResult(
            ".mcp/server-config.json", True,
            f"{GREEN}OK{RESET} ({MCP_CONFIG.stat().st_size:,} bytes)",
            "Configuración MCP V8.0"
        ))
    else:
        memory_results.append(CheckResult(
            ".mcp/server-config.json", False,
            f"{YELLOW}FALTANTE{RESET}",
            "Configuración MCP V8.0"
        ))

    # Verificar si DSPy está disponible
    try:
        import dspy
        memory_results.append(CheckResult(
            "dspy-ai", True,
            f"{GREEN}INSTALADO{RESET} v{getattr(dspy, '__version__', '?')}",
            "DSPy — Optimización de prompts V8.0"
        ))
    except ImportError:
        memory_results.append(CheckResult(
            "dspy-ai", False,
            f"{YELLOW}NO INSTALADO{RESET} — pip install dspy-ai",
            "DSPy — Optimización de prompts V8.0 (opcional)"
        ))

    # Verificar RAG index
    brain_meta = AGENT_DIR / "BRAIN" / "index_metadata.json"
    if brain_meta.exists():
        import json as _json
        try:
            meta = _json.loads(brain_meta.read_text())
            indexed_at = meta.get("indexed_at", "?")
            chunks = meta.get("stats", {}).get("chunks", 0)
            memory_results.append(CheckResult(
                "RAG index (ChromaDB)", True,
                f"{GREEN}ACTIVO{RESET} ({chunks} chunks)",
                f"Indexado: {indexed_at[:16]}"
            ))
        except Exception:
            memory_results.append(CheckResult("RAG index", False, f"{YELLOW}CORRUPTO{RESET}", ""))
    else:
        memory_results.append(CheckResult(
            "RAG index (ChromaDB)", False,
            f"{YELLOW}NO INICIALIZADO{RESET}",
            "Ejecutar: python3 .agent/BRAIN/bootstrap.py --full"
        ))

    all_results.extend(memory_results)

    # Ítems V8.0 opcionales (requieren pip install adicional)
    V8_OPTIONAL_NAMES = {"dspy-ai", "RAG index (ChromaDB)", ".agent/BRAIN/vector_store"}

    for r in memory_results:
        if not args.json:
            print_result(r, args.quiet)
        if not r.ok:
            if (
                "freshness" in r.name
                or "FALTANTE" in r.message
                or "VACÍO" in r.message
                or r.name in V8_OPTIONAL_NAMES
                or "opcional" in r.detail.lower()
                or "pip install" in r.detail.lower()
            ):
                warnings += 1
            else:
                errors += 1

    if not args.quiet and not args.json:
        print_section("2. Estado Actual (STATE.md)")
        print_state_summary()

    # ── 3. Archivos del Proyecto ──────────────────────────────────────────
    if not args.quiet and not args.json:
        print_section("3. Archivos Críticos del Proyecto")

    project_results = check_project_files()
    all_results.extend(project_results)

    for r in project_results:
        if not args.json:
            print_result(r, args.quiet)
        if not r.ok:
            errors += 1

    # ── 4. Configuración de Entorno ───────────────────────────────────────
    if not args.quiet and not args.json:
        print_section("4. Configuración de Entorno (.env)")

    env_results = check_env_config()
    all_results.extend(env_results)

    for r in env_results:
        if not args.json:
            print_result(r, args.quiet)
        if not r.ok:
            if "opcional" in r.detail.lower() or "opcional" in r.message.lower():
                warnings += 1
            else:
                errors += 1

    # ── 5. Dependencias Python (solo con --full) ───────────────────────────
    if args.full:
        if not args.quiet and not args.json:
            print_section("5. Dependencias Python")

        dep_results = check_python_deps()
        all_results.extend(dep_results)

        for r in dep_results:
            if not args.json:
                print_result(r, args.quiet)
            if not r.ok:
                if "opcional" in r.message.lower():
                    warnings += 1
                else:
                    errors += 1

    # ── 6. Servicios Docker (solo con --full) ─────────────────────────────
    if args.full:
        if not args.quiet and not args.json:
            print_section("6. Servicios de Infraestructura")
            print(f"  {DIM}(Si los servicios están en Docker con red interna, pueden aparecer OFFLINE){RESET}")

        service_results = check_optional_services()
        all_results.extend(service_results)

        for r in service_results:
            if not args.json:
                print_result(r, args.quiet)
            if not r.ok:
                warnings += 1  # Servicios offline son solo advertencias

    # ── Resumen Final ─────────────────────────────────────────────────────
    if args.json:
        output = {
            "timestamp": datetime.now().isoformat(),
            "errors": errors,
            "warnings": warnings,
            "all_ok": errors == 0,
            "checks": [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "message": r.message.replace(GREEN, "").replace(RED, "").replace(YELLOW, "").replace(RESET, ""),
                    "detail": r.detail,
                }
                for r in all_results
            ]
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0 if errors == 0 else 1

    # Resumen visual
    width = 66
    print(f"\n{BOLD}{'═' * width}{RESET}")
    if errors == 0 and warnings == 0:
        print(f"{GREEN}{BOLD}  ✅ ¡Todo listo! El agente está completamente operativo.{RESET}")
    elif errors == 0:
        print(f"{YELLOW}{BOLD}  ⚠️  Operativo con {warnings} advertencia(s). Revisa los items amarillos.{RESET}")
    else:
        print(f"{RED}{BOLD}  ❌  Atención: {errors} error(es) crítico(s) encontrado(s).{RESET}")
        print(f"{RED}     Resuelve los items rojos antes de continuar.{RESET}")

    print(f"{DIM}  Errores: {errors} | Advertencias: {warnings} | Total checks: {len(all_results)}{RESET}")
    print(f"{BOLD}{'═' * width}{RESET}")

    # Recordatorio del protocolo
    if not args.quiet:
        print(f"\n{CYAN}{BOLD}📋 Protocolo de sesión:{RESET}")
        print(f"  1. {DIM}Leer{RESET} .agent/CONTEXT.md       → Visión del proyecto")
        print(f"  2. {DIM}Leer{RESET} .agent/MAP.md           → Arquitectura técnica")
        print(f"  3. {DIM}Leer{RESET} .agent/STATE.md         → Estado actual y deudas")
        print(f"  4. {DIM}Leer{RESET} .agent/RULES.md         → Hard rules innegociables")
        print(f"  5. {DIM}RAG:{RESET}  .agent/BRAIN/bootstrap.py --status")
        print(f"  6. {DIM}Al terminar →{RESET} Actualizar .agent/STATE.md + ADR si aplica\n")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
