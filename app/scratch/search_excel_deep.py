import pandas as pd

excel_path = 'campos formas precodificadas.xlsx'
xl = pd.ExcelFile(excel_path)

for sheet in xl.sheet_names:
    df = pd.read_excel(xl, sheet)
    # Search in all columns
    mask = df.apply(lambda x: x.astype(str).str.contains('68', case=False)).any(axis=1)
    found = df[mask]
    if not found.empty:
        print(f"--- FOUND IN SHEET: {sheet} ---")
        print(found)
        print("\n")
