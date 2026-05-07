import re
import uuid
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class HeuristicsEngine:
    """
    Motor de Ingeniería Legal basado en Heurísticas (H1-H7).
    Optimiza la extracción de datos y reduce la dependencia de LLMs.
    """

    def __init__(self):
        # H5: Marcadores léxicos invariantes para clasificación
        self.doc_markers = {
            "boleta_inscripcion": ["BOLETA DE INSCRIPCION", "FOLIO REAL", "ASIENTO"],
            "certificado_libertad": ["CERTIFICADO DE LIBERTAD DE GRAVAMEN", "CERTIFICA", "NO EXISTE"],
            "escritura_publica": ["ESCRITURA NUMERO", "PROTOCOLO", "NOTARIO PUBLICO"],
            "titulo_propiedad": ["TITULO DE PROPIEDAD", "PROPIETARIO", "REGISTRO"],
            "testamento": ["TESTAMENTO", "VOLUNTAD", "HEREDERO", "LEGATARIO", "ALBACEA"],
            "persona_moral": ["ACTA CONSTITUTIVA", "ASAMBLEA", "SOCIOS", "CAPITAL SOCIAL", "DENOMINACION"],
            "bienes_muebles": ["BIEN MUEBLE", "VEHICULO", "FACTURA", "NUMERO DE SERIE"],
        }

        # H7: Palabras de corte para limpieza de datos
        self.cutoff_words = [
            "CC", "TIPO", "ACCIONADO", "IDENTIFICADO", 
            "FECHA", "NOTARIO", "INSCRIPCION", "ASIENTO"
        ]

        # Cargar contexto legal para heurísticas semánticas
        self.semantic_markers = self._load_legal_context()

    def _load_legal_context(self) -> Dict[str, List[str]]:
        """Carga los labels de legal_context.json como marcadores secundarios."""
        try:
            import os
            import json
            base_dir = os.path.dirname(__file__)
            path = os.path.join(base_dir, "legal_context.json")
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Extraer solo los labels únicos por acto
                    markers = {}
                    for act, fields in data.items():
                        unique_labels = list(set([str(f.get('label', '')).upper() for f in fields if f.get('label')]))
                        markers[act] = unique_labels
                    return markers
        except Exception as e:
            logger.error(f"Error cargando contexto semántico para heurísticas: {e}")
        return {}

    # H5: Clasificación por densidad de marcadores (Híbrida: Invariantes + Semánticos)
    def classify_document(self, text: str) -> str:
        text_start = text[:3000].upper()
        scores = {}
        
        # 1. Marcadores Invariantes (Peso alto)
        for doc_type, markers in self.doc_markers.items():
            score = sum(5 for marker in markers if marker in text_start) # Peso 5
            scores[doc_type] = score
        
        # 2. Marcadores Semánticos (Labels del catálogo/Excel - Peso bajo por ser más genéricos)
        for act_id, labels in self.semantic_markers.items():
            # Si el acto ya tiene score por invariantes, lo acumulamos
            # Solo revisamos los primeros 20 labels para no saturar
            current_score = scores.get(act_id, 0)
            semantic_score = sum(1 for label in labels[:20] if label in text_start)
            scores[act_id] = current_score + semantic_score
        
        if not scores:
            return "desconocido"

        best_match = max(scores, key=scores.get)
        if scores[best_match] > 0:
            return best_match
        return "desconocido"

    # H7: Filtro por palabras de corte
    def clean_extracted_value(self, value: str) -> str:
        if not isinstance(value, str):
            return value
        
        for word in self.cutoff_words:
            # Buscar palabra de corte con límites de palabra (\b)
            pattern = r'\b' + re.escape(word) + r'\b'
            match = re.search(pattern, value, re.IGNORECASE)
            if match:
                value = value[:match.start()].strip()
        
        return value.strip()

    # H6: Validación cruzada de interdependencias lógicas
    def validate_consistency(self, data: Dict[str, Any]) -> List[str]:
        alerts = []
        # Ejemplo: Fecha de registro no puede ser anterior a fecha de escritura
        fecha_reg = data.get("fecha_registro")
        fecha_esc = data.get("fecha_escritura")
        
        if fecha_reg and fecha_esc:
            try:
                # Simplificación: asumiendo formato ISO o similar
                d_reg = datetime.fromisoformat(str(fecha_reg))
                d_esc = datetime.fromisoformat(str(fecha_esc))
                if d_reg < d_esc:
                    alerts.append("H6 Violation: Fecha de registro anterior a fecha de escritura")
            except:
                pass
        
        return alerts

    # H1: Priorización por confiabilidad del identificador
    def get_primary_identifier(self, data: Dict[str, Any]) -> Optional[str]:
        # Prioridad: Folio Real > Clave Catastral > Escritura > Nombre
        for key in ["folio_real", "clave_catastral", "numero_escritura", "nombre_solicitante"]:
            if data.get(key):
                return str(data.get(key))
        return None

    # H2: Puntuación multi-criterio para duplicados
    def calculate_similarity_score(self, doc1: Dict[str, Any], doc2: Dict[str, Any]) -> float:
        score = 0.0
        # Pesos heurísticos
        weights = {
            "folio_real": 0.8,
            "clave_catastral": 0.7,
            "numero_escritura": 0.5,
            "nombre": 0.2
        }
        
        for key, weight in weights.items():
            if doc1.get(key) and doc1.get(key) == doc2.get(key):
                score += weight
        
        return min(score, 1.0)

    def fix_encoding(self, text: str) -> str:
        if not isinstance(text, str): return text
        try:
            if "Ã" in text or "Â" in text:
                return text.encode('latin1').decode('utf8')
        except: pass
        return text

    # Aplicar todas las heurísticas de limpieza post-extracción
    def post_process_extraction(self, extracted_data: Dict[str, Any]) -> Dict[str, Any]:
        cleaned_data = {}
        for k, v in extracted_data.items():
            clean_key = self.fix_encoding(k)
            if isinstance(v, str):
                cleaned_data[clean_key] = self.fix_encoding(self.clean_extracted_value(v))
            elif isinstance(v, list):
                cleaned_data[clean_key] = [self.post_process_extraction(i) if isinstance(i, dict) else self.fix_encoding(self.clean_extracted_value(i)) for i in v]
            elif isinstance(v, dict):
                cleaned_data[clean_key] = self.post_process_extraction(v)
            else:
                cleaned_data[clean_key] = v
        return cleaned_data

heuristics = HeuristicsEngine()
