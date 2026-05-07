
import asyncio
import os
import sys

# Detectar si estamos dentro del contenedor (el código está en /app) o fuera
if os.path.exists("/app/db/database.py"):
    # Estamos dentro del contenedor
    sys.path.append("/app")
else:
    # Estamos fuera, añadir ./app
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "app")))

try:
    from db.database import engine
    from db.models import Base
    from seed_catalog import seed
except ImportError:
    from app.db.database import engine
    from app.db.models import Base
    from app.seed_catalog import seed

from sqlalchemy import text

async def bootstrap():
    print("🚀 Iniciando Bootstrap de IDP-Smart Database...")
    
    async with engine.begin() as conn:
        print("Ensuring schema idp_smart exists...")
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS idp_smart"))
        
        print("Creating/Updating tables from models.py...")
        # Esto creará document_extractions, process_logs, act_forms_catalog y hardware_benchmarks
        await conn.run_sync(Base.metadata.create_all)
    
    print("Seeding catalog...")
    await seed()
    
    print("✅ Bootstrap completado exitosamente!")

if __name__ == "__main__":
    asyncio.run(bootstrap())
