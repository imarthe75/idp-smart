import time
import os
import sys
import json
import logging
import uuid
import tempfile
import fitz # PyMuPDF
from concurrent.futures import ThreadPoolExecutor, as_completed

# Setup path for imports
sys.path.append("/app")

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend

def benchmark_docling(file_path: str, num_threads: int, batch_size: int, concurrent_batches: int):
    print(f"--- STARTING BENCHMARK: THREADS={num_threads} | CHUNK={batch_size} | CONCURRENCY={concurrent_batches} ---")
    
    # 1. Transform TIF to PDF if needed
    if file_path.lower().endswith((".tif", ".tiff")):
        with fitz.open(file_path) as doc:
            pdf_bytes = doc.convert_to_pdf()
            temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf").name
            with open(temp_pdf, "wb") as f:
                f.write(pdf_bytes)
            file_path = temp_pdf
    
    # 2. Setup Converter
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.do_table_structure = True
    pipeline_options.accelerator_options.device = "cpu"
    pipeline_options.accelerator_options.num_threads = num_threads
    
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options, backend=PyPdfiumDocumentBackend)}
    )
    
    # 3. Read total pages
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(file_path)
    total_pages = len(reader.pages)
    print(f"Document Pages: {total_pages}")
    
    start_total = time.time()
    
    def process_chunk(chunk_id, start_p, end_p):
        chunk_pdf = os.path.join(tempfile.gettempdir(), f"bench_chunk_{uuid.uuid4()}.pdf")
        writer = PdfWriter()
        for idx in range(start_p, end_p):
            writer.add_page(reader.pages[idx])
        with open(chunk_pdf, "wb") as f:
            writer.write(f)
        
        t_start = time.time()
        res = converter.convert(chunk_pdf)
        t_end = time.time()
        os.remove(chunk_pdf)
        return t_end - t_start

    results = []
    if concurrent_batches <= 1:
        # Serial
        for start in range(0, total_pages, batch_size):
            end = min(start + batch_size, total_pages)
            t = process_chunk(start // batch_size, start, end)
            results.append(t)
            print(f"  Chunk {(start//batch_size)+1} Done: {t:.2f}s")
    else:
        # Parallel
        with ThreadPoolExecutor(max_workers=concurrent_batches) as executor:
            futures = {}
            for start in range(0, total_pages, batch_size):
                end = min(start + batch_size, total_pages)
                futures[executor.submit(process_chunk, start // batch_size, start, end)] = start // batch_size
            
            for future in as_completed(futures):
                t = future.result()
                results.append(t)
                print(f"  Chunk in parallel Done: {t:.2f}s")

    end_total = time.time()
    total_duration = end_total - start_total
    sec_per_page = total_duration / total_pages
    
    print(f"TOTAL: {total_duration:.2f}s | AVG/PAGE: {sec_per_page:.2f}s")
    return {
        "threads": num_threads,
        "batch_size": batch_size,
        "concurrency": concurrent_batches,
        "total_time": total_duration,
        "sec_per_page": round(sec_per_page, 3)
    }

if __name__ == "__main__":
    sample = "/app/samples/57deaf06-2fea-41ed-9719-aa48b84dbf72.pdf"
    if not os.path.exists(sample):
        print(f"Sample not found: {sample}")
        sys.exit(1)
    
    final_results = []
    
    # Test A: Standard 12 threads @ 10 pages Serial
    final_results.append(benchmark_docling(sample, num_threads=12, batch_size=10, concurrent_batches=1))
    
    # Test B: 6 threads @ 5 pages Parallel (2 batches)
    final_results.append(benchmark_docling(sample, num_threads=6, batch_size=5, concurrent_batches=2))

    print("\n=== FINAL COMPARISON ===")
    print(json.dumps(final_results, indent=2))
    
    with open("/app/samples/benchmark_results.json", "w") as f:
        json.dump(final_results, f, indent=2)
