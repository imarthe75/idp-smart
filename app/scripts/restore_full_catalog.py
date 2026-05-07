
import re
import json
import os
import sys
from sqlalchemy import create_engine, text

# Añadir el path actual para importar gen_legal_context_v2
sys.path.append(os.getcwd())

# Conectar a la BD según el entorno
DATABASE_URL = "postgresql://admin:casmarts_secure_db_pwd_2026@172.18.0.16:5432/db_core_knowledge"
engine = create_engine(DATABASE_URL)

sql_file = '/home/ia/idp-smart/db/init-db.sql'

def restore():
    if not os.path.exists(sql_file):
        print(f"Error: {sql_file} not found")
        return

    print(f"Leyendo {sql_file}...")
    with open(sql_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Regex para capturar los bloques de inserción
    # form_code, lldeffrmpre, llacto, dsactocorta, dsacto, jsconfforma
    pattern = re.compile(
        r"VALUES\s*\(\s*"
        r"'(\d+)',\s*"          # form_code
        r"(\d+),\s*"            # lldeffrmpre
        r"(\d+),\s*"            # llacto
        r"'([^']+)',\s*"        # dsactocorta
        r"'([^']+)',\s*"        # dsacto
        r"E'(.*?)'"             # jsconfforma (JSON)
        r"\s*\);", 
        re.DOTALL
    )
    
    matches = pattern.findall(content)
    
    print(f"Encontrados {len(matches)} actos para restaurar.")
    
    with engine.begin() as conn:
        print("Limpiando tabla idp_smart.act_forms_catalog...")
        conn.execute(text("TRUNCATE TABLE idp_smart.act_forms_catalog RESTART IDENTITY"))
        
        count = 0
        for m in matches:
            form_code, lldeffrmpre, llacto, dsactocorta, dsacto, jsconfforma = m
            
            # El JSON en Postgres E'...' puede tener escapes de tipo C.
            # Intentamos decodificarlo.
            try:
                # Reemplazar comillas simples duplicadas (escape de SQL)
                js_sql_cleaned = jsconfforma.replace("''", "'")
                # Intentar cargar directamente primero
                try:
                    js_obj = json.loads(js_sql_cleaned)
                except:
                    # Si falla, intentar evaluar escapes de unicode/hex
                    js_obj = json.loads(js_sql_cleaned.encode('utf-8').decode('unicode_escape'))
            except Exception as e:
                print(f"Error parseando JSON para {dsactocorta}: {e}")
                # Si falla mucho, tal vez imprimir un fragmento
                # print(f"Fragmento: {js_sql_cleaned[:200]}")
                continue

            conn.execute(text("""
                INSERT INTO idp_smart.act_forms_catalog 
                (form_code, lldeffrmpre, llacto, dsactocorta, dsacto, jsconfforma)
                VALUES (:form_code, :lldeffrmpre, :llacto, :dsactocorta, :dsacto, :jsconfforma)
            """), {
                "form_code": form_code,
                "lldeffrmpre": int(lldeffrmpre),
                "llacto": int(llacto),
                "dsactocorta": dsactocorta,
                "dsacto": dsacto,
                "jsconfforma": json.dumps(js_obj)
            })
            count += 1
            
    print(f"✅ Restauración completada: {count} registros insertados.")

    # 2. Re-generar el contexto legal
    print("Actualizando engine/legal_context.json...")
    try:
        import gen_legal_context_v2
        print("Contexto legal actualizado.")
    except Exception as e:
        print(f"Error actualizando contexto legal: {e}")

if __name__ == "__main__":
    restore()
