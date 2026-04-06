import requests
import time
import sys

TASK_IDS = [
    "019d4641-5300-7c7d-88c6-600bcbbaf5cc",
    "019d466f-4b36-7393-9db1-62a161321284"
]

BASE_URL = "http://localhost:8000/api/v1"

def reprocess_and_wait(task_id):
    print(f"\n>> Reprocesando Tarea: {task_id}")
    res = requests.post(f"{BASE_URL}/reprocess/{task_id}?skip_vision=false")
    if res.status_code != 200:
        print(f"Error encolando {task_id}: {res.text}")
        return
    
    print("Encolado exitosamente. Monitoreando base de datos...")
    
    while True:
        status_res = requests.get(f"{BASE_URL}/progress/{task_id}")
        if status_res.status_code == 200:
            data = status_res.json()
            pct = data.get("progress_pct", 0)
            stage = data.get("stage_label", "")
            status = data.get("status", "")
            
            # Limpiar línea
            sys.stdout.write(f"\rProgreso: {pct}% | Etapa: {stage} | Estado: {status}       ")
            sys.stdout.flush()
            
            if status in ["COMPLETED", "COMPLETADO", "ERROR"] or str(status).startswith("ERROR"):
                print(f"\nFinalizado con estado: {status}")
                
                # Fetching logs
                logs_res = requests.get(f"{BASE_URL}/logs/{task_id}")
                if logs_res.status_code == 200:
                    ldata = logs_res.json()
                    print(f"Duración Total del proceso documentada: {ldata.get('total_duration_s')}s")
                    for log in ldata.get("logs", []):
                        print(f"  [{log['stage']}] {log['message']} ({log['duration_ms']}ms)")
                        if log['detail']:
                            print(f"      -> {log['detail']}")
                break
        time.sleep(3)

if __name__ == "__main__":
    for tid in TASK_IDS:
        reprocess_and_wait(tid)
