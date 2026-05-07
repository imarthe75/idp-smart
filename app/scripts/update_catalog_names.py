
import pandas as pd
import os
from sqlalchemy import create_engine, text

# Conectar a la BD según el entorno
DATABASE_URL = "postgresql://admin:casmarts_secure_db_pwd_2026@172.18.0.16:5432/db_core_knowledge"
engine = create_engine(DATABASE_URL)

def update_catalog():
    excel_path = 'campos formas precodificadas.xlsx'
    if not os.path.exists(excel_path):
        print(f"No se encontró el archivo {excel_path}")
        return

    print(f"Leyendo nombres desde {excel_path}...")
    xl = pd.ExcelFile(excel_path)
    names_map = {}
    
    for sheet in ['_estructura', '_estructura_bmpmt']:
        if sheet in xl.sheet_names:
            df = pd.read_excel(xl, sheet_name=sheet)
            # El acto suele estar en la primera columna, la descripción en la segunda
            for _, row in df.iterrows():
                acto = str(row.iloc[0]).lower().strip()
                desc = str(row.iloc[1]).strip()
                if acto and desc and acto not in names_map:
                    names_map[acto] = desc
    
    print(f"Procesando {len(names_map)} nombres únicos desde ambas hojas...")

    with engine.begin() as conn:
        for acto, desc in names_map.items():
            acto_clean = str(acto).lower().strip()
            # Actualizar dsacto donde dsactocorta coincida
            conn.execute(
                text("UPDATE idp_smart.act_forms_catalog SET dsacto = :desc WHERE LOWER(dsactocorta) = :acto"),
                {"desc": desc, "acto": acto_clean}
            )
    
    print("Sincronización de nombres completada.")

if __name__ == "__main__":
    update_catalog()
