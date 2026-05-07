
import json
import uuid
import asyncio
import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Database connection from environment or fallback
DB_USER = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "casmarts_secure_db_pwd_2026")
DB_HOST = os.getenv("DB_HOST", "casmarts-core-db-primary")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "db_core_knowledge")

DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine = create_async_engine(DATABASE_URL)

async def seed():
    # 1. Intentar cargar el seed maestro (Estructura autoritativa)
    master_seed_path = 'engine/full_catalog_seed.json'
    master_data = None
    if os.path.exists(master_seed_path):
        print(f"✅ Cargando estructura maestra desde {master_seed_path}...")
        with open(master_seed_path, 'r', encoding='utf-8') as f:
            master_data = json.load(f)
    
    # 2. Fallback a legal_context (Estructura genérica)
    legal_context = None
    if not master_data:
        ctx_path = 'engine/legal_context.json'
        if os.path.exists(ctx_path):
            print(f"⚠️ Seed maestro no encontrado. Usando contexto legal genérico de {ctx_path}...")
            with open(ctx_path, 'r', encoding='utf-8') as f:
                legal_context = json.load(f)
        else:
            print(f"❌ Error: No se encontró ni el seed maestro ni el contexto legal.")
            return
    
    async with engine.begin() as conn:
        print("Ensuring schema idp_smart exists...")
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS idp_smart"))
        
        print("Ensuring table act_forms_catalog exists...")
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS idp_smart.act_forms_catalog (
                id SERIAL PRIMARY KEY,
                form_code VARCHAR(50),
                lldeffrmpre INTEGER,
                llacto INTEGER,
                dsactocorta VARCHAR(100),
                dsacto VARCHAR(255),
                jsconfforma JSONB,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))

        # Limpiar para carga limpia
        await conn.execute(text("TRUNCATE TABLE idp_smart.act_forms_catalog RESTART IDENTITY"))

        if master_data:
            print(f"Inyectando {len(master_data)} actos desde el seed maestro...")
            for item in master_data:
                await conn.execute(text("""
                    INSERT INTO idp_smart.act_forms_catalog 
                    (form_code, lldeffrmpre, llacto, dsactocorta, dsacto, jsconfforma)
                    VALUES (:fc, :lp, :la, :sc, :ds, :js)
                """), {
                    "fc": item.get("form_code"),
                    "lp": item.get("lldeffrmpre"),
                    "la": item.get("llacto"),
                    "sc": item.get("dsactocorta"),
                    "ds": item.get("dsacto"),
                    "js": json.dumps(item.get("jsconfforma"))
                })
        else:
            print(f"Inyectando {len(legal_context)} actos (Generando campos genéricos)...")
            for act_id, fields in legal_context.items():
                # Lógica genérica original
                sections = {}
                for f in fields:
                    if not isinstance(f, dict): continue
                    sec = f.get('section', 'General')
                    if sec not in sections: sections[sec] = []
                    sections[sec].append(f.get('label', 'Campo'))
                
                jsconfforma = {
                    "name": act_id.upper(),
                    "label": f"Formulario {act_id.upper()}",
                    "containers": []
                }
                
                for sec_name, field_labels in sections.items():
                    container = {
                        "name": sec_name.lower().replace(" ", "_"),
                        "label": sec_name,
                        "fields": []
                    }
                    unique_labels = []
                    for lbl in field_labels:
                        if lbl not in unique_labels: unique_labels.append(lbl)
                    for label in unique_labels:
                        container["fields"].append({
                            "uuid": str(uuid.uuid4()),
                            "name": label.lower().replace(" ", "_"),
                            "label": label,
                            "type": "text"
                        })
                    jsconfforma["containers"].append(container)

                await conn.execute(text("""
                    INSERT INTO idp_smart.act_forms_catalog 
                    (form_code, dsactocorta, dsacto, jsconfforma)
                    VALUES (:code, :short, :desc, :js)
                """), {
                    "code": act_id.upper(),
                    "short": act_id.upper(),
                    "desc": f"Acto {act_id.upper()}",
                    "js": json.dumps(jsconfforma)
                })

    print("✅ Seeding completed successfully!")

if __name__ == "__main__":
    asyncio.run(seed())
