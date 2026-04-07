import pandas as pd
import json
import os
from sqlalchemy import create_engine, text

# Conectar a la BD según el entorno
DATABASE_URL = "postgresql://admin_user:Ad54=Tx91.Vm+23_Qr78@idp_db:5432/rpp"
engine = create_engine(DATABASE_URL)

mapping = {}

# 1. Obtener base técnica de la Base de Datos (jsconfforma)
try:
    with engine.connect() as conn:
        print("Analizando act_forms_catalog en la BD...")
        res = conn.execute(text("SELECT dsactocorta, form_code, dsacto, jsconfforma FROM idp_smart.act_forms_catalog"))
        for row in res:
            act_short = str(row[0]).lower().strip()
            form_code = str(row[1])
            desc_db = str(row[2])
            js = row[3]
            
            if not js or 'containers' not in js: continue
            
            if act_short not in mapping: mapping[act_short] = []
            
            # Recorrer contenedores (secciones) en el nuevo formato
            for container in js.get('containers', []):
                section_name = container.get('label', container.get('name', 'General'))
                for field in container.get('fields', []):
                    mapping[act_short].append({
                        "section": section_name,
                        "label": field.get('label', field.get('name', 'Campo')),
                        "source": "database",
                        "act_desc": desc_db,
                        "form_code": form_code
                    })
    print(f"Detectados {len(mapping)} actos (basados en dsactocorta) en la BD.")
except Exception as e:
    print(f"Error BD: {e}")

# 2. Complementar con Excel (Contexto Semántico)
try:
    if os.path.exists('campos formas precodificadas.xlsx'):
        print("Complementando con Excel...")
        xl = pd.ExcelFile('campos formas precodificadas.xlsx')
        for sheet in ['_estructura', '_estructura_bmpmt']:
            if sheet in xl.sheet_names:
                df = pd.read_excel(xl, sheet)
                for _, row in df.iterrows():
                    acto = str(row.iloc[0]).lower().strip()
                    section_excel = str(row.iloc[3])
                    label_excel = str(row.iloc[4])
                    
                    if acto in mapping:
                        # Añadir como sugerencia semántica
                        mapping[acto].append({
                            "section": section_excel,
                            "label": label_excel,
                            "source": "excel"
                        })
    print("Mapeo híbrido completado.")
except Exception as e:
    print(f"Error Excel: {e}")

# 3. Guardar versión final
try:
    final_path = 'engine/legal_context.json'
    with open(final_path, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print(f"Contexto legal actualizado en {final_path}")
except Exception as e:
    print(f"Error guardando: {e}")
