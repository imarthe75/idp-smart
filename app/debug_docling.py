import os
import sys
import uuid
import tempfile
from pypdf import PdfReader
from engine.vision_optimized import extract_markdown_from_minio_sync
from core.config import settings

def test_manual(object_name):
    print(f"Testing Docling with object: {object_name}")
    try:
        md, p_count, strategy = extract_markdown_from_minio_sync(object_name)
        print(f"Result: pages={p_count}, strategy={strategy}")
        print(f"Markdown snippet: {md[:100]}...")
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 debug_docling.py <object_name>")
    else:
        test_manual(sys.argv[1])
