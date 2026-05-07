import os
from sqlalchemy import create_engine, text

dbs = ['postgres', 'db_core_knowledge', 'consultarpp_db', 'consultarpp', 'lexivault']
password = "casmarts_secure_db_pwd_2026"
host = "172.18.0.16"

for db in dbs:
    try:
        url = f"postgresql://admin:{password}@{host}:5432/{db}"
        engine = create_engine(url)
        with engine.connect() as conn:
            res = conn.execute(text("SELECT table_schema, table_name FROM information_schema.tables WHERE table_name = 'cfdeffrmpre'"))
            for row in res:
                print(f"FOUND cfdeffrmpre in DB: {db}, Schema: {row[0]}")
            
            res2 = conn.execute(text("SELECT table_schema, table_name FROM information_schema.tables WHERE table_name = 'ctactos'"))
            for row in res2:
                print(f"FOUND ctactos in DB: {db}, Schema: {row[0]}")
    except Exception as e:
        print(f"Error connecting to {db}: {e}")
