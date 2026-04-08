from core.storage_client import get_storage_client
import json

def get_json_schema(bucket: str, object_name: str) -> dict:
    """Extrae el JSON subido al endpoint para saber qué buscar del documento."""
    client = get_storage_client()
    try:
        response = client.get_object(bucket, object_name)
        data = response.read()
        return json.loads(data.decode("utf-8"))
    except Exception as e:
        print(f"Error descargando el JSON del bucket {bucket}/{object_name}: {e}")
        return []
    finally:
        if 'response' in locals():
            response.close()
            response.release_conn()

def map_results_to_json(json_schema: dict, extracted_data: dict) -> dict:
    """
    Inyecta datos extraídos al JSON original respetando la estructura exacta.
    
    Comportamiento:
    - uuid → valor simple: inyecta directamente
    - uuid → array: inyecta como array (datos repetibles)
    - uuid → objeto: inyecta como objeto (datos anidados)
    
    NO modifica la estructura del esquema, solo rellena valores.
    """
    import copy

    def _recurse_inject(node):
        """Recorre el árbol del esquema e inyecta datos."""
        if isinstance(node, dict):
            node_uuid = node.get("uuid")
            
            # Si este UUID existe en los datos extraídos
            if node_uuid and node_uuid in extracted_data:
                extracted_value = extracted_data[node_uuid]
                node["value"] = extracted_value
            
            # Continuar recursión en propiedades del nodo
            for key, v in list(node.items()):
                if key != "value":  # No procesar el value que acabamos de inyectar
                    _recurse_inject(v)
                    
        elif isinstance(node, list):
            for item in node:
                _recurse_inject(item)
    
    # Clonar el esquema para no modificar original
    result = copy.deepcopy(json_schema)
    _recurse_inject(result)
    
    return result

def extract_fields_from_schema(schema: dict) -> list:
    """Extrae todos los campos (uuid, label, type) de forma plana para pasárselos al LLM."""
    fields = []
    def _collect(node):
        if isinstance(node, dict):
            # Si tiene UUID y Label, es algo extraíble (campo o contenedor)
            if node.get("uuid") and node.get("label"):
                fields.append({
                    "uuid": node["uuid"],
                    "label": node["label"],
                    "type": node.get("type", "container"),
                    "is_repetitive": node.get("repetitiva", False)
                })
            for v in node.values():
                _collect(v)
        elif isinstance(node, list):
            for item in node:
                _collect(item)
    _collect(schema)
    return fields
