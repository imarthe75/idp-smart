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
    Soporta campos repetitivos duplicando nodos si es necesario.
    """
    import copy

    def _process_node(node, data_context):
        """
        Procesa un nodo individual (dict). 
        data_context es el diccionario donde buscamos el valor para este nodo.
        """
        if not isinstance(node, dict):
            return node

        node_uuid = node.get("uuid")
        is_repetitive = node.get("repetitiva", False)
        
        # 1. Inyectar valor si existe en el contexto actual
        if node_uuid and node_uuid in data_context:
            val = data_context[node_uuid]
            
            # Si el valor es una lista y el nodo es repetitivo,
            # este nivel de recursión no duplicará (eso se hace en el padre al procesar la lista)
            # simplemente asignamos el valor si es un tipo simple.
            if not isinstance(val, (list, dict)):
                node["value"] = val
            elif isinstance(val, dict):
                # Si es un objeto, lo asignamos y seguimos procesando hijos con este contexto
                node["value"] = "" # Limpiar value si es contenedor
                # El contexto para los hijos ahora es este objeto
                _process_children(node, val)
                return node
            elif isinstance(val, list) and not is_repetitive:
                # Fallback: si recibimos lista pero no es repetitivo, tomamos el primer valor
                node["value"] = val[0] if val else ""
        
        # 2. Procesar hijos (controls y containers) con el contexto actual
        _process_children(node, data_context)
        return node

    def _process_children(node, data_context):
        """Procesa las listas de controles y contenedores de un nodo."""
        for key in ["controls", "fields", "containers"]:
            if key in node and isinstance(node[key], list):
                new_list = []
                for child in node[key]:
                    child_uuid = child.get("uuid")
                    # Si el hijo es repetitivo y tenemos una lista de datos para él
                    if child.get("repetitiva") and child_uuid and isinstance(data_context.get(child_uuid), list):
                        data_list = data_context[child_uuid]
                        for entry in data_list:
                            # Clonar el hijo y procesarlo con el contexto de esta entrada
                            cloned_child = copy.deepcopy(child)
                            # Si la entrada es un dict, es el nuevo contexto, si no (valor simple), 
                            # pasamos un contexto artificial {uuid: valor} para que lo encuentre.
                            ctx = entry if isinstance(entry, dict) else {child_uuid: entry}
                            new_list.append(_process_node(cloned_child, ctx))
                    else:
                        # Nodo normal, seguir recursión
                        new_list.append(_process_node(child, data_context))
                node[key] = new_list

    # El esquema suele estar envuelto en una estructura raíz o ser el objeto directamente
    result = copy.deepcopy(json_schema)
    if isinstance(result, list):
        return [_process_node(item, extracted_data) for item in result]
    return _process_node(result, extracted_data)

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
