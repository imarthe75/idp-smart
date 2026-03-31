import os
import sys
import time
import json
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Any
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from concurrent.futures import ProcessPoolExecutor, as_completed
import pypdf

# Setup paths to include app
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

SAMPLES_DIR = Path("/app/samples")
RESULTS_DIR = Path("/app/ocr_outputs")
RESULTS_DIR.mkdir(exist_ok=True)
BENCH_LOG_FILE = Path("/app/benchmark_outputs.json")

def get_page_count(pdf_path):
    try:
        reader = pypdf.PdfReader(pdf_path)
        return len(reader.pages)
    except:
        return 0

def process_single_doc(pdf_path: str, save_output: bool = True):
    """Procesamiento síncrono para ser llamado por ProcessPoolExecutor"""
    t0 = time.time()
    try:
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True
        # En paralelo masivo, forzamos 1 hilo por proceso para no sobrecárgar
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["MKL_NUM_THREADS"] = "1"
        
        converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        
        result = converter.convert(pdf_path)
        md = result.document.export_to_markdown()
        elapsed = time.time() - t0
        
        if save_output:
            out_file = RESULTS_DIR / (Path(pdf_path).stem + ".md")
            with open(out_file, "w") as f:
                f.write(md)

        return {
            "status": "success",
            "time": elapsed,
            "chars": len(md),
            "pages": get_page_count(pdf_path),
            "error": None
        }
    except Exception as e:
        return {
            "status": "error",
            "time": time.time() - t0,
            "chars": 0,
            "pages": 0,
            "error": str(e)
        }

async def run_benchmark(concurrency: int):
    """
    Ejecuta un benchmark con una configuración específica.
    """
    logger.info(f"🚀 Iniciando Benchmark: Concurrency={concurrency}")
    
    test_files = list(SAMPLES_DIR.glob("*.pdf"))
    if not test_files:
        return None

    start_time = time.time()
    total_pages = 0
    results = []
    
    # Usamos ProcessPoolExecutor para aislar cores
    with ProcessPoolExecutor(max_workers=concurrency) as executor:
        futures = []
        for pdf in test_files:
            futures.append(executor.submit(process_single_doc, str(pdf), True))
        
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            total_pages += res["pages"]
            
    total_time = time.time() - start_time
    success_rate = len([r for r in results if r["status"] == "success"]) / len(results)
    throughput = total_pages / total_time if total_time > 0 else 0
    
    metrics = {
        "concurrency": concurrency,
        "total_time": total_time,
        "total_pages": total_pages,
        "throughput_pg_sec": throughput,
        "success_rate": success_rate,
        "timestamp": time.time()
    }
    
    logger.info(f"�� Resultado: {throughput:.2f} pgs/sec | Éxito: {success_rate*100:.1f}%")
    return metrics

async def main():
    if not SAMPLES_DIR.exists():
        logger.error(f"Directory {SAMPLES_DIR} not found")
        return

    # Configuraciones de concurrencia a testear (de menos a más agresivo para 48 cores)
    concurrency_configs = [4, 8, 12, 16, 24, 32, 40, 48]
    
    all_metrics = []
    
    print("\n--- INICIO DE PROCESAMIENTO Y BENCHMARKING (48 CORES) ---\n")
    
    for concurrency in concurrency_configs:
        try:
            # En cada paso, procesamos TODOS los archivos de samples/ y guardamos sus MDs
            # así se cumple la petición de procesar y además benchmarkear.
            metrics = await run_benchmark(concurrency)
            if metrics:
                all_metrics.append(metrics)
                # Pequeño respiro entre tests (aunque ya se procesaron todos)
                await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Error en config ({concurrency}): {e}")

    # Guardar resultados
    with open(BENCH_LOG_FILE, "w") as f:
        json.dump(all_metrics, f, indent=2)
    
    # Encontrar el mejor
    if all_metrics:
        best = max(all_metrics, key=lambda x: x["throughput_pg_sec"])
        
        print("\n" + "="*50)
        print("🏆 CONFIGURACIÓN ÓPTIMA DETECTADA")
        print("="*50)
        print(f"Concurrency: {best['concurrency']} (núcleos)")
        print(f"Throughput:  {best['throughput_pg_sec']:.2f} pgs/sec")
        print(f"Total Time:  {best['total_time']:.1f}s")
        print("="*50 + "\n")
        
        print(f"Archivos Markdowns generados en: {RESULTS_DIR}")
        print(f"Log detallado guardado en:      {BENCH_LOG_FILE}")
    else:
        print("No se obtuvieron métricas.")

if __name__ == "__main__":
    asyncio.run(main())
