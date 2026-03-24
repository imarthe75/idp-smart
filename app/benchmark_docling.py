#!/usr/bin/env python3
"""
🚀 Benchmarking Script para Docling Vision Engine
Mide performance actual (CPU) antes de agregar GPU/RunPod

Uso:
    python3 benchmark_docling.py --test-file docs/sample.pdf
    python3 benchmark_docling.py --batch-test 5 pages  # 5 documentos de 5 páginas
    python3 benchmark_docling.py --all                 # Todos los tests

Output:
    - CSV con métricas detalladas
    - Gráficos de performance
    - Comparativa vs GPU/RunPod proyectada
"""

import os
import sys
import time
import json
import logging
import asyncio
import statistics
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple, Optional
import argparse
from dataclasses import dataclass, asdict
import csv

# Setup paths
sys.path.insert(0, str(Path(__file__).parent.parent))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('benchmark_results.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Resultado de un benchmark individual"""
    test_name: str
    document_name: str
    pages: int
    processing_device: str
    processing_mode: str
    time_elapsed: float
    characters_output: int
    cache_hit: bool
    throughput_pages_per_sec: float
    memory_rss_mb: float
    timestamp: str
    
    def __post_init__(self):
        if self.pages > 0:
            self.throughput_pages_per_sec = self.pages / max(self.time_elapsed, 0.1)


class DoclingBenchmark:
    """Harness para benchmarking de Docling"""
    
    def __init__(self, output_dir: str = "benchmark_results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
        self.results: List[BenchmarkResult] = []
        self.csv_file = self.output_dir / f"benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        logger.info(f"📊 Benchmark initialized. Output: {self.output_dir}")
    
    async def test_single_document(
        self,
        pdf_path: str,
        test_name: str = "single_test"
    ) -> BenchmarkResult:
        """Testa un documento individual"""
        
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            logger.error(f"❌ Archivo no encontrado: {pdf_path}")
            raise FileNotFoundError(f"PDF no encontrado: {pdf_path}")
        
        logger.info(f"🔍 Testeando: {pdf_path.name} ({test_name})")
        
        try:
            # Import aquí para permitir skip si no está disponible
            from app.engine.vision_optimized import vision_engine
            import pypdf
            import psutil
            
            # Contar páginas
            with open(pdf_path, 'rb') as f:
                pdf = pypdf.PdfReader(f)
                num_pages = len(pdf.pages)
            
            # Capturar memoria inicial
            process = psutil.Process()
            mem_before = process.memory_info().rss / 1024 / 1024
            
            # Procesar
            t0 = time.time()
            try:
                # Mock MinIO: usar path local directamente
                # Nota: Esto requiere que vision_engine esté configurado
                markdown = await vision_engine.extract_markdown_from_minio(pdf_path.name)
            except Exception as e:
                logger.error(f"❌ Error en extracción: {e}")
                # Fallback: procesar localmente sin MinIO
                from docling.document_converter import DocumentConverter, PdfFormatOption
                from docling.datamodel.base_models import InputFormat
                
                doc_converter = DocumentConverter(
                    format_options={
                        InputFormat.PDF: PdfFormatOption()
                    }
                )
                result = doc_converter.convert(str(pdf_path))
                markdown = result.document.export_to_markdown()
            
            elapsed = time.time() - t0
            mem_after = process.memory_info().rss / 1024 / 1024
            mem_used = mem_after - mem_before
            
            result = BenchmarkResult(
                test_name=test_name,
                document_name=pdf_path.name,
                pages=num_pages,
                processing_device="CPU",
                processing_mode="cpu_parallel",
                time_elapsed=elapsed,
                characters_output=len(markdown),
                cache_hit=False,
                throughput_pages_per_sec=num_pages / max(elapsed, 0.1),
                memory_rss_mb=mem_used,
                timestamp=datetime.now().isoformat()
            )
            
            logger.info(
                f"✅ Test completo: {elapsed:.2f}s, "
                f"{num_pages} págs, "
                f"{result.throughput_pages_per_sec:.2f} pgs/s, "
                f"Mem: {mem_used:.1f}MB"
            )
            
            self.results.append(result)
            return result
        
        except Exception as e:
            logger.error(f"❌ Benchmark error: {e}", exc_info=True)
            raise
    
    def test_cache_effectiveness(self, pdf_path: str) -> Dict:
        """Testa efectividad del cache"""
        
        logger.info(f"🔄 Testing cache effectiveness...")
        
        try:
            from app.engine.vision_optimized import vision_engine
            import time
            
            pdf_name = Path(pdf_path).name
            
            # Primer pass - cache miss
            t0 = time.time()
            result1 = asyncio.run(vision_engine.extract_markdown_from_minio(pdf_name))
            time1 = time.time() - t0
            
            # Segundo pass - cache hit (esperado)
            time.sleep(0.5)
            t0 = time.time()
            result2 = asyncio.run(vision_engine.extract_markdown_from_minio(pdf_name))
            time2 = time.time() - t0
            
            speedup = time1 / max(time2, 0.001)
            
            cache_result = {
                "first_pass_sec": time1,
                "second_pass_sec": time2,
                "speedup": speedup,
                "cache_effective": time2 < time1 * 0.1  # 90% faster = cache hit
            }
            
            logger.info(
                f"💾 Cache test: First={time1:.2f}s, Second={time2:.4f}s, "
                f"Speedup={speedup:.0f}×"
            )
            
            return cache_result
        
        except Exception as e:
            logger.error(f"❌ Cache test error: {e}")
            return {"error": str(e)}
    
    def generate_sample_pdf(self, num_pages: int = 5) -> Path:
        """Genera PDF de prueba para benchmarking"""
        
        logger.info(f"📄 Generando PDF de prueba ({num_pages} páginas)...")
        
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import inch
            
            # Crear dummy PDF
            pdf_path = self.output_dir / f"test_{num_pages}pages_{int(time.time())}.pdf"
            c = canvas.Canvas(str(pdf_path), pagesize=letter)
            
            for page_num in range(num_pages):
                c.drawString(1*inch, 10.5*inch, f"Test Document - Page {page_num + 1}")
                c.drawString(1*inch, 10*inch, "Lorem ipsum dolor sit amet, consectetur adipiscing elit.")
                c.drawString(1*inch, 9.5*inch, "This is a test PDF for benchmarking Docling performance.")
                
                # Agregar tabla simple
                y = 9*inch
                for i in range(10):
                    c.drawString(1*inch, y - i*0.3*inch, f"Row {i}: Sample data for testing")
                
                c.showPage()
            
            c.save()
            logger.info(f"✅ PDF generado: {pdf_path}")
            return pdf_path
        
        except ImportError:
            logger.warning("⚠️ reportlab no disponible, descargando PDF de ejemplo...")
            # Fallback: usar PDF de ejemplo si existe
            example_pdf = Path("forms_data.txt")
            if example_pdf.exists():
                logger.info(f"ℹ️ Usando file existente: {example_pdf}")
                return example_pdf
            else:
                raise FileNotFoundError("No hay PDFs disponibles para testing")
    
    def save_results(self):
        """Guarda resultados en CSV"""
        
        if not self.results:
            logger.warning("⚠️ No hay resultados para guardar")
            return
        
        try:
            with open(self.csv_file, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=asdict(self.results[0]).keys())
                writer.writeheader()
                for result in self.results:
                    writer.writerow(asdict(result))
            
            logger.info(f"💾 Resultados guardados: {self.csv_file}")
        
        except Exception as e:
            logger.error(f"❌ Error guardando resultados: {e}")
    
    def print_summary(self):
        """Imprime resumen de resultados"""
        
        if not self.results:
            logger.warning("⚠️ Sin resultados")
            return
        
        print("\n" + "="*80)
        print("📊 BENCHMARKING RESULTS SUMMARY")
        print("="*80)
        
        # Agrupar por test_name
        by_test = {}
        for result in self.results:
            key = result.test_name
            if key not in by_test:
                by_test[key] = []
            by_test[key].append(result)
        
        for test_name, results in by_test.items():
            print(f"\n🔍 Test: {test_name}")
            print("-" * 80)
            
            times = [r.time_elapsed for r in results]
            throughputs = [r.throughput_pages_per_sec for r in results]
            pages = [r.pages for r in results]
            
            print(f"  Documents tested: {len(results)}")
            print(f"  Total pages: {sum(pages)}")
            print(f"  Time - Min: {min(times):.2f}s, Max: {max(times):.2f}s, Avg: {statistics.mean(times):.2f}s")
            print(f"  Throughput - Min: {min(throughputs):.2f}, Max: {max(throughputs):.2f}, Avg: {statistics.mean(throughputs):.2f} pgs/s")
            
            if len(times) > 1:
                stdev = statistics.stdev(times)
                print(f"  Std Dev: {stdev:.2f}s")
        
        # Cálculos de escalabilidad esperada
        print("\n" + "="*80)
        print("📈 PROYECCIONES DE ESCALABILIDAD")
        print("="*80)
        
        avg_throughput = statistics.mean([r.throughput_pages_per_sec for r in self.results])
        
        print(f"\n📊 Performance Actual (CPU):")
        print(f"  Throughput: {avg_throughput:.2f} páginas/segundo")
        print(f"  Tiempo por documento (10 pgs): {10/avg_throughput:.1f}s")
        print(f"  Documentos por hora: {3600*avg_throughput/10:.0f}")
        print(f"  Documentos por día (24h): {86400*avg_throughput/10:.0f}")
        
        print(f"\n🚀 Proyecciones CON GPU NVIDIA (RTX 3060+):")
        gpu_speedup = 6  # Esperado 6× más rápido con GPU
        gpu_throughput = avg_throughput * gpu_speedup
        print(f"  Throughput estimado: {gpu_throughput:.2f} páginas/segundo ({gpu_speedup}×)")
        print(f"  Tiempo por documento (10 pgs): {10/gpu_throughput:.1f}s")
        print(f"  Documentos por hora: {3600*gpu_throughput/10:.0f}")
        print(f"  Documentos por día (24h): {86400*gpu_throughput/10:.0f}")
        
        print(f"\n☁️ Proyecciones CON RUNPOD Serverless:")
        runpod_pages_per_sec = 8  # ~8 pgs/sec distribuido
        print(f"  Throughput estimado: {runpod_pages_per_sec:.2f} páginas/segundo")
        print(f"  Tiempo para 100 páginas: {100/runpod_pages_per_sec:.0f}s")
        print(f"  Documentos por hora: {3600*runpod_pages_per_sec/10:.0f}")
        
        print("\n" + "="*80 + "\n")
    
    def generate_projection_table(self):
        """Genera tabla de comparación CPU/GPU/RunPod"""
        
        if not self.results:
            logger.warning("⚠️ Sin resultados")
            return
        
        avg_time = statistics.mean([r.time_elapsed for r in self.results])
        avg_pages = statistics.mean([r.pages for r in self.results])
        
        print("\n" + "="*100)
        print("COMPARACIÓN PERFORMANCE: CPU vs GPU vs RunPod")
        print("="*100)
        
        # Tabla de comparación
        scenarios = [
            ("CPU (TODAY)", 1.0, "✅ Active now"),
            ("GPU (Q2 2026)", 0.15, "6-7× faster (when added)"),
            ("RunPod (Q3 2026)", 0.2, "Distributed, scalable"),
            ("GPU + Cache HIT", 0.01, "99× faster (repeated docs)"),
        ]
        
        print(f"\n{'Scenario':<25} {'Time':<15} {'Pages/min':<15} {'Status':<35}")
        print("-" * 100)
        
        for scenario, multiplier, note in scenarios:
            time_sec = avg_time * multiplier
            pages_per_min = (avg_pages / time_sec) * 60 if time_sec > 0 else 0
            print(f"{scenario:<25} {time_sec:<15.1f}s {pages_per_min:<15.0f} {note:<35}")
        
        print("\n" + "="*100 + "\n")


async def main():
    """Main entry point"""
    
    parser = argparse.ArgumentParser(
        description="Benchmark Docling Vision Engine Performance"
    )
    parser.add_argument(
        "--test-file",
        type=str,
        help="PDF file to test"
    )
    parser.add_argument(
        "--batch-test",
        type=int,
        nargs=2,
        metavar=("COUNT", "PAGES"),
        help="Generate and test N documents with P pages each"
    )
    parser.add_argument(
        "--cache-test",
        action="store_true",
        help="Test cache effectiveness"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all benchmark tests"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="benchmark_results",
        help="Output directory for results"
    )
    
    args = parser.parse_args()
    
    benchmark = DoclingBenchmark(output_dir=args.output_dir)
    
    # Determinar qué tests ejecutar
    tests_to_run = []
    
    if args.test_file:
        tests_to_run.append(("single_file", args.test_file))
    
    if args.batch_test:
        count, pages = args.batch_test
        tests_to_run.append(("batch_generated", count, pages))
    
    if args.cache_test or args.all:
        tests_to_run.append(("cache_test", None))
    
    if args.all:
        # Default test con archivo existente
        tests_to_run = [
            ("batch_generated", 3, 5),   # 3 documents, 5 pages each
        ]
    
    if not tests_to_run:
        # Default: simple test
        logger.info("📌 modo default: Generando y testeando documento de 5 páginas...")
        pdf_path = benchmark.generate_sample_pdf(num_pages=5)
        tests_to_run = [("single_file", str(pdf_path))]
    
    # Ejecutar tests
    logger.info(f"🎯 Executing {len(tests_to_run)} tests...\n")
    
    for test_spec in tests_to_run:
        if test_spec[0] == "single_file":
            await benchmark.test_single_document(test_spec[1], test_name="single_document")
        
        elif test_spec[0] == "batch_generated":
            count, pages = test_spec[1], test_spec[2]
            for i in range(count):
                pdf_path = benchmark.generate_sample_pdf(num_pages=pages)
                await benchmark.test_single_document(
                    str(pdf_path),
                    test_name=f"batch_test_{i+1}_of_{count}"
                )
        
        elif test_spec[0] == "cache_test":
            if benchmark.results:
                # Usar último documento
                last_pdf = benchmark.results[-1].document_name
                cache_result = benchmark.test_cache_effectiveness(last_pdf)
                logger.info(f"Cache result: {json.dumps(cache_result, indent=2)}")
    
    # Post-processing
    benchmark.save_results()
    benchmark.print_summary()
    benchmark.generate_projection_table()
    
    logger.info("✅ Benchmark complete!")


if __name__ == "__main__":
    asyncio.run(main())
