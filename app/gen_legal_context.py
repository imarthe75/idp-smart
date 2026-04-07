import pandas as pd
import json
import os

try:
    xl = pd.ExcelFile('campos formas precodificadas.xlsx')
    d1 = pd.read_excel(xl, '_estructura')
    d2 = pd.read_excel(xl, '_estructura_bmpmt')
    
    mapping = {}
    
    for df in [d1, d2]:
        for idx, row in df.iterrows():
            acto = str(row.iloc[0]).lower().strip()
            if acto not in mapping:
                mapping[acto] = []
            mapping[acto].append({
                "section": str(row.iloc[3]),
                "label": str(row.iloc[4])
            })
            
    with open('engine/legal_context.json', 'w', encoding='utf-8') as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print("Legal context generated successfully.")
except Exception as e:
    print(f"Error generating legal context: {e}")
