import os
import sys
import time
import json
from pathlib import Path
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
import pypdf

sys.path.insert(0, "/app")

SAMPLES_DIR = Path("/app/samples")
RESULTS_DIR = Path("/app/ocr_outputs")
RESULTS_DIR.mkdir(exist_ok=True)

def get_page_count(pdf_path):
    try:
        reader = pypdf.PdfReader(pdf_path)
        return len(reader.pages)
    except:
        return 0

def main():
    test_files = list(SAMPLES_DIR.glob("*.pdf"))
    
    print(f"\n🚀 PROCESANDO {len(test_files)} DOCUMENTOS SECUENCIALMENTE (LIMITADO POR RAM)\n")
    print(f"{'Filename':<40} | {'Pages':<6} | {'Time':<8} | {'Speed':<8}")
    print("-" * 75)

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.do_table_structure = True
    
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )

    stats = []
    
    for pdf in test_files:
        t0 = time.time()
        try:
            pages = get_page_count(pdf)
            result = converter.convert(str(pdf))
            md = result.document.export_to_markdown()
            elapsed = time.time() - t0
            
            with open(RESULTS_DIR / (pdf.stem + ".md"), "w") as f:
                f.write(md)
            
            print(f"{pdf.name:<40} | {pages:<6} | {elapsed:<8.2f}s | {pages/elapsed:<8.2f} pgs/s")
            stats.append({"file": pdf.name, "pages": pages, "time": elapsed})
        except Exception as e:
            print(f"{pdf.name:<40} | ERROR: {str(e)[:40]}")

    total_pages = sum(s["pages"] for s in stats)
    total_time = sum(s["time"] for s in stats)
    
    print("\n" + "="*75)
    print(f"RESUMEN (1 CORE): {total_pages/total_time:.2f} pgs/sec")
    print(f"RESUMEN (8 WORKERS ESTIMADO): {(total_pages/total_time)*8:.2f} pgs/sec")
    print("="*75 + "\n")

if __name__ == "__main__":
    main()
