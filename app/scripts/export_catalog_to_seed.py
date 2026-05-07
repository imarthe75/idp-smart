
import json
import os
from sqlalchemy import create_engine, text

# Conectar a la BD según el entorno
DATABASE_URL = "postgresql://admin:casmarts_secure_db_pwd_2026@172.18.0.16:5432/db_core_knowledge"
engine = create_engine(DATABASE_URL)

def export_seed():
    output_path = 'engine/full_catalog_seed.json'
    print(f"Exportando catálogo actual desde la BD a {output_path}...")
    
    with engine.connect() as conn:
        query = text("""
            SELECT form_code, lldeffrmpre, llacto, dsactocorta, dsacto, jsconfforma 
            FROM idp_smart.act_forms_catalog
            ORDER BY id ASC
        """)
        result = conn.execute(query)
        
        seed_data = []
        for row in result:
            seed_data.append({
                "form_code": row[0],
                "lldeffrmpre": row[1],
                "llacto": row[2],
                "dsactocorta": row[3],
                "dsacto": row[4],
                "jsconfforma": row[5]
            })
            
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(seed_data, f, ensure_ascii=False, indent=2)
        
    print(f"✅ Exportación exitosa. {len(seed_data)} actos guardados.")

if __name__ == "__main__":
    export_seed()
