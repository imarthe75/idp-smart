
import asyncio
import sys
import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Mock settings for internal Docker network
DB_USER = "admin_user"
DB_PASSWORD = "Ad54=Tx91.Vm+23_Qr78"
DB_HOST = "db"
DB_PORT = "5432"
DB_NAME = "rpp"

DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# We need to send to Celery
from celery import Celery
celery_app = Celery("idp_worker", broker="redis://valkey:6379/0", backend="redis://valkey:6379/0")

async def recover_tasks():
    engine = create_async_engine(DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        # Buscamos tareas en el limbo
        query = text("""
            SELECT task_id, json_storage_path, pdf_storage_path 
            FROM idp_smart.document_extractions 
            WHERE status = 'PENDING_CELERY' OR status = 'INICIO'
        """)
        result = await session.execute(query)
        rows = result.fetchall()
        
        print(f"🔎 Encontradas {len(rows)} tareas para recuperar...")
        
        for row in rows:
            tid, json_path, pdf_path = row
            
            # Limpieza de para el worker: El worker espera rutas RELATIVAS al bucket.
            # Nuestras rutas en DB son "idp-documents/UUID/archivo.pdf"
            # El worker sumará "idp-documents" + path.
            # Por lo tanto, debemos enviar "UUID/archivo.pdf"
            
            clean_pdf = pdf_path.replace("idp-documents/", "")
            clean_json = json_path.replace("idp-documents/", "")
            
            print(f"🚀 Re-encolando tarea {tid}...")
            print(f"   PDF: {clean_pdf}")
            print(f"   JSON: {clean_json}")
            
            celery_app.send_task(
                "process_doc",
                args=[str(tid), clean_json, clean_pdf, False],
                task_id=str(tid)
            )
            
            # Asegurarnos de que esté en PENDING_CELERY
            update = text("UPDATE idp_smart.document_extractions SET status = 'PENDING_CELERY' WHERE task_id = :tid")
            await session.execute(update, {"tid": tid})
        
        await session.commit()
    print("✅ Recuperación completada.")

if __name__ == "__main__":
    asyncio.run(recover_tasks())
