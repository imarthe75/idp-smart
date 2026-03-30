"""
Hardware Detector — idp-smart
------------------------------
Detecta automáticamente si hay GPU disponible y calcula
los parámetros óptimos de paralelismo para CPU.

Resultado usado por ocr_factory y smart_router para
seleccionar el modo de ejecución adecuado.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache

logger = logging.getLogger(__name__)


@dataclass
class HardwareProfile:
    has_gpu: bool
    gpu_name: str
    gpu_vram_mb: int
    cpu_cores: int
    ram_total_gb: float
    # Parámetros de ejecución derivados
    docling_device: str        # "cuda" | "cpu"
    omp_threads: int           # núcleos para OpenMP (docling/numpy)
    mkl_threads: int           # núcleos para MKL (intel math)
    pdf_chunk_size: int        # páginas por chunk en modo CPU
    max_parallel_batches: int  # Lotes concurrentes en ThreadPoolExecutor
    processing_unit: str       # etiqueta para hardware_benchmarks


@lru_cache(maxsize=1)
def detect_hardware() -> HardwareProfile:
    """
    Inspecciona el hardware disponible una sola vez (cached).
    Método: intenta llamar nvidia-smi; si falla, asume CPU.
    """
    # CPU detectando afinidad real (Docker cpuset)
    try:
        # sched_getaffinity devuelve el conjunto de núcleos permitidos para el proceso actual
        cpu_cores = len(os.sched_getaffinity(0))
    except (AttributeError, Exception):
        cpu_cores = os.cpu_count() or 4

    # RAM total en GB
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    ram_kb = int(line.split()[1])
                    ram_gb = ram_kb / (1024 ** 2)
                    break
            else:
                ram_gb = 16.0
    except Exception:
        ram_gb = 16.0

    # ── GPU detection ────────────────────────────────────────────────────────
    has_gpu = False
    gpu_name = "none"
    gpu_vram_mb = 0

    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                first_gpu = result.stdout.strip().splitlines()[0]
                parts = [p.strip() for p in first_gpu.split(",")]
                gpu_name = parts[0]
                gpu_vram_mb = int(parts[1]) if len(parts) > 1 else 0
                has_gpu = True
        except Exception as exc:
            logger.warning("nvidia-smi disponible pero falló: %s", exc)

    # ── Derivar parámetros óptimos ────────────────────────────────────────────
    if has_gpu:
        docling_device = "cuda"
        processing_unit = f"GPU:{gpu_name}"
        # En GPU usamos todos los cores CPU para I/O
        omp_threads = min(cpu_cores, 8)
        mkl_threads = min(cpu_cores, 8)
        pdf_chunk_size = 999  # Sin chunking en GPU (memoria suficiente)
        max_parallel_batches = 1 # En GPU procesamos linealmente o gestionamos VRAM
    else:
        docling_device = "cpu"
        processing_unit = "CPU"
        # En un worker dedicado (Celery), usamos TODOS sus núcleos asignados (cpuset)
        # pero dejamos 1 libre para orquestación interna
        omp_threads = max(1, cpu_cores - 1)
        mkl_threads = omp_threads
        
        # ── ADAPTACIÓN DINÁMICA DE CHUNK SIZE ───────────────────────────
        # Si está en el .env como un número, lo respetamos.
        # Si no, calculamos el óptimo según hardware:
        env_chunk = os.environ.get("DOCLING_CHUNK_SIZE", "auto")
        if env_chunk.isdigit():
            pdf_chunk_size = int(env_chunk)
        else:
            # --- Cálculo de Tamaño de Chunk (Seguridad RAM 2GB) ---
            # 48GB total -> ~24 chunks máx para seguridad absoluta.
            max_chunks_by_ram = max(4, int(ram_gb // 2.0))
            
            # Estimación por CPU (base 6, +1 cada 2 núcleos extra)
            ideal_chunks_by_cpu = max(6, min(30, 6 + (cpu_cores - 8) // 2))
            
        # El chunk final respeta el límite de RAM
        pdf_chunk_size = min(ideal_chunks_by_cpu, max_chunks_by_ram)
        
        # --- Cálculo de Lotes Paralelos (Celery ThreadPool) ---
        # 1 batch cada 8 núcleos, con tope de 6 para no saturar bus RAM
        max_parallel_batches = max(1, min(6, cpu_cores // 8)) 
        
        logger.info("⚙️ [AUTO-TUNE] pdf_chunk_size: %d | batches: %d", pdf_chunk_size, max_parallel_batches)

        # En modo masivo paralelo, forzamos 1 hilo interno para evitar sobre-saturación
        omp_threads = 1
        mkl_threads = 1

    profile = HardwareProfile(
        has_gpu=has_gpu,
        gpu_name=gpu_name,
        gpu_vram_mb=gpu_vram_mb,
        cpu_cores=cpu_cores,
        ram_total_gb=round(ram_gb, 1),
        docling_device=docling_device,
        omp_threads=omp_threads,
        mkl_threads=mkl_threads,
        pdf_chunk_size=pdf_chunk_size,
        max_parallel_batches=max_parallel_batches if not has_gpu else 1,
        processing_unit=processing_unit,
    )

    logger.info(
        "HardwareProfile: GPU=%s (%s) | CPU=%d cores | RAM=%.1f GB | "
        "device=%s | omp=%d | chunk=%d páginas",
        has_gpu,
        gpu_name,
        cpu_cores,
        ram_gb,
        docling_device,
        omp_threads,
        pdf_chunk_size,
    )
    return profile


def apply_thread_limits(profile: HardwareProfile) -> None:
    """
    Aplica las variables de entorno de paralelismo antes de importar
    cualquier librería que use OpenMP/MKL (docling, torch, numpy, etc.)
    Debe llamarse lo más temprano posible en el proceso worker.
    """
    if os.environ.get("OMP_NUM_THREADS") in (None, "0", ""):
        os.environ["OMP_NUM_THREADS"] = str(profile.omp_threads)
    if os.environ.get("MKL_NUM_THREADS") in (None, "0", ""):
        os.environ["MKL_NUM_THREADS"] = str(profile.mkl_threads)
    if os.environ.get("OPENBLAS_NUM_THREADS") in (None, "0", ""):
        os.environ["OPENBLAS_NUM_THREADS"] = str(profile.omp_threads)
    if os.environ.get("NUMEXPR_NUM_THREADS") in (None, "0", ""):
        os.environ["NUMEXPR_NUM_THREADS"] = str(profile.omp_threads)

    logger.info(
        "Thread limits aplicados: OMP=%s MKL=%s (calculado OMP=%d)",
        os.environ.get("OMP_NUM_THREADS"),
        os.environ.get("MKL_NUM_THREADS"),
        profile.omp_threads,
    )
