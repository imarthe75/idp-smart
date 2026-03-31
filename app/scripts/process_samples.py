import os
import sys
import time
import json
import logging
from pathlib import Path
from typing import List, Dict, Any
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from concurrent.futures import ProcessPoolExecutor, as_completed
import pypdf

# Setup paths to include app
sys.path.insert(0, "/app")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

SAMPLES_DIR = Path("/app/samples")
RESULTS_DIR = Path("/app/ocr_outputs")
RESULTS_DIR.mkdir(exist_ok=True)
STATS_FILE = Path("/app/processing_stats.json")

def get_page_count(pdf_path):
    try:
        reader = pypdf.PdfReader(pdf_path)
        return len(reader.pages)
    except:
        return 0

def process_single_doc(pdf_path: str):
    t0 = time.time()
    try:
        pages = get_page_count(pdf_path)
        
        # Parámetros óptimos para 6 cores por proceso (8 procesos en total)
        os.environ["OMP_NUM_THREADS"] = "6" 
        os.environ["MKL_NUM_THREADS"] = "6"
        
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True
        
        converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        
        result = converter.convert(pdf_path)
        md = result.document.export_to_markdown()
        elapsed = time.time() - t0
        
        out_file = RESULTS_DIR / (Path(pdf_path).stem + ".md")
        with open(out_file, "w") as f:
            f.write(md)

        return {
            "file": Path(pdf_path).name,
            "status": "success",
            "pages": pages,
            "time_sec": round(elapsed, 2),
            "pg_per_sec": round(pages / elapsed, 2) if elapsed > 0 else 0
        }
    except Exception as e:
        return {
            "file": Path(pdf_path).name,
            "status": "error",
            "pages": 0,
            "time_sec": round(time.time() - t0, 2),
            "error": str(e)
        }

def main():
    if not SAMPLES_DIR.exists():
        logger.error(f"Directory {SAMPLES_DIR} not found")
        return

    test_files = list(SAMPLES_DIR.glob("*.pdf"))
    if not test_files:
        logger.error("No PDF files found")
        return

    # Usamos 8 workers para no saturar los 47GB de RAM (8 * 4GB = 32GB aprox)
    concurrency = 8
    all_stats = []
    
    print(f"\n🚀 PROCESANDO {len(test_files)} DOCUMENTOS EN LOS 48 NÚCLEOS (8 WORKERS x 6 CORES)\n")
    print(f"{'Filename':<40} | {'Pages':<6} | {'Time':<8} | {'Speed':<8}")
    print("-" * 75)

    with ProcessPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(process_single_doc, str(pdf)): pdf for pdf in test_files}
        
        for future in as_completed(futures):
            res = future.result()
            all_stats.append(res)
            if res["status"] == "success":
                print(f"{res['file']:<40} | {res['pages']:<6} | {res['time_sec']:<8.2f}s | {res['pg_per_sec']:<8.2f} pgs/s")
            else:
                print(f"{res['file']:<40} | ERROR: {res['error'][:40]}")

    # Guardar estadísticas
    with open(STATS_FILE, "w") as f:
        json.dump(all_stats, f, indent=2)
    
    total_pages = sum(s["pages"] for s in all_stats if s["status"] == "success")
    total_time = sum(s["time_sec"] for s in all_stats if s["status"] == "success")
    avg_speed = total_pages / total_time if total_time > 0 else 0
    
    print("\n" + "="*75)
    print("📊 RESUMEN FINAL DE PROCESAMIENTO")
    print("="*75)
    print(f"Total Páginas: {total_pages}")
    print(f"Archivos OK:   {len([s for s in all_stats if s['status'] == 'success'])}")
    print(f"Velocidad Promedio (Host Completo): {avg_speed:.2f} páginas/segundo")
    print(f"Proyección: {(avg_speed * 60):.0f} páginas por minuto")
    print("="*75 + "\n")

if __name__ == "__main__":
    main()
