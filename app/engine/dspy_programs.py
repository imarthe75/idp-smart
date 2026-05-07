"""
dspy_programs.py — Módulo DSPy para Extracción Notarial idp-smart
=================================================================
Implementa programas DSPy que reemplazan los prompts f-string hardcodeados
en agent.py. DSPy se autocalibra según el modelo LLM y los ejemplos de
documentos notariales del corpus RPP.

FILOSOFÍA:
  - El agente NO escribe prompts fijos.
  - Escribe Programas DSPy que se optimizan automáticamente.
  - Cada programa es un módulo reutilizable y trazable.

ESTRUCTURA:
  1. Signatures (contratos entrada/salida)
  2. Modules (lógica de razonamiento)
  3. Optimizers (calibración con ejemplos del corpus)
  4. Programs (combinación de modules para tareas completas)

USO:
  from engine.dspy_programs import NotarialExtractionProgram

  program = NotarialExtractionProgram()
  result = program(
      markdown_content=markdown,
      schema_map="{uuid: label}",
      act_type="Compraventa",
  )
  print(result.extracted_json)

DEPENDENCIAS:
  pip install dspy-ai
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── DSPy ─────────────────────────────────────────────────────────────────────
try:
    import dspy
    DSPY_AVAILABLE = True
except ImportError:
    DSPY_AVAILABLE = False
    logger.warning(
        "DSPy no está instalado. Usar: pip install dspy-ai\n"
        "Fallback activo: se usarán los prompts tradicionales de agent.py"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. SIGNATURES — Contratos de Entrada/Salida
# ─────────────────────────────────────────────────────────────────────────────

if DSPY_AVAILABLE:

    class NotarialExtractionSignature(dspy.Signature):
        """
        Extrae datos de un documento notarial mexicano y genera un JSON plano.

        El documento ha sido convertido a Markdown por un OCR (Docling).
        El esquema define los campos a extraer mediante un mapa UUID → Label.
        REGLAS CRÍTICAS:
          1. Extrae LITERALMENTE — no resumas ni normalices nombres propios.
          2. Si un campo no existe en el documento → valor null.
          3. Si una sección es repetitiva (múltiples comparecientes) → lista.
          4. Devuelve SOLO el JSON puro, sin explicaciones ni markdown adicional.
          5. RFC: 13 chars (persona física) / 12 chars (persona moral).
          6. CURP: exactamente 18 caracteres.
          7. Folio Real Electrónico: exactamente 20 dígitos.
        """
        # Inputs
        markdown_content: str = dspy.InputField(
            desc="Contenido OCR del documento notarial en formato Markdown"
        )
        schema_map: str = dspy.InputField(
            desc="Mapa JSON de {UUID: Label} con los campos a extraer del esquema"
        )
        act_type: str = dspy.InputField(
            desc="Tipo de acto notarial (ej. Compraventa, Donación, Hipoteca)"
        )
        legal_context: str = dspy.InputField(
            desc="Secciones del esquema que deben estar presentes en este tipo de acto",
            default=""
        )

        # Output
        extracted_json: str = dspy.OutputField(
            desc="JSON plano con {UUID: valor_extraído}. Listas para campos repetitivos. "
                 "Null para campos no encontrados. SIN texto adicional."
        )


    class DocumentClassifierSignature(dspy.Signature):
        """
        Clasifica el tipo de acto notarial de un documento.
        Identifica el rol semántico de cada compareciente (Enajenante/Adquirente).
        """
        markdown_excerpt: str = dspy.InputField(
            desc="Primeros 2000 caracteres del documento OCR"
        )

        act_type: str = dspy.OutputField(
            desc="Tipo de acto inferido: Compraventa | Donación | Hipoteca | Primera Inscripción | Otro"
        )
        enajenante_signal: str = dspy.OutputField(
            desc="Término usado para 'Enajenante' en este documento (ej. Vendedor, Donante, Deudor)"
        )
        adquirente_signal: str = dspy.OutputField(
            desc="Término usado para 'Adquirente' en este documento (ej. Comprador, Donatario, Acreedor)"
        )
        confidence: str = dspy.OutputField(
            desc="Nivel de confianza: alta | media | baja"
        )


    class PIIValidatorSignature(dspy.Signature):
        """
        Valida la integridad de datos PII extraídos de un documento notarial.
        Detecta posibles errores de OCR en campos críticos.
        """
        extracted_pii: str = dspy.InputField(
            desc="JSON con campos PII extraídos: RFC, CURP, FRE, nombres"
        )

        validation_result: str = dspy.OutputField(
            desc="JSON con {campo: {valid: bool, issue: str|null, corrected: str|null}}"
        )
        has_critical_errors: bool = dspy.OutputField(
            desc="True si hay errores que impiden guardar el documento"
        )


    class ChunkMergerSignature(dspy.Signature):
        """
        Fusiona resultados de extracción de múltiples chunks de un documento largo.
        Resuelve conflictos (mismo UUID con valores distintos) priorizando el más completo.
        """
        chunk_results: str = dspy.InputField(
            desc="JSON array con los resultados de cada chunk: [{uuid: valor}, ...]"
        )
        schema_map: str = dspy.InputField(
            desc="Mapa UUID → Label para validar que los UUIDs son conocidos"
        )

        merged_json: str = dspy.OutputField(
            desc="JSON unificado con {UUID: valor_final}. Conflictos resueltos."
        )
        conflicts_resolved: str = dspy.OutputField(
            desc="Lista de UUIDs donde se resolvió un conflicto y la estrategia usada"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. MODULES — Lógica de Razonamiento
# ─────────────────────────────────────────────────────────────────────────────

if DSPY_AVAILABLE:

    class NotarialExtractor(dspy.Module):
        """
        Módulo principal de extracción semántica notarial.
        Usa Chain-of-Thought para mejorar la precisión en documentos complejos.
        """

        def __init__(self, use_cot: bool = True):
            super().__init__()
            if use_cot:
                self.extractor = dspy.ChainOfThought(NotarialExtractionSignature)
            else:
                self.extractor = dspy.Predict(NotarialExtractionSignature)

        def forward(
            self,
            markdown_content: str,
            schema_map: str,
            act_type: str,
            legal_context: str = "",
        ) -> dspy.Prediction:
            # Para documentos muy largos, truncar con indicador
            MAX_CONTENT = 50_000
            if len(markdown_content) > MAX_CONTENT:
                markdown_content = (
                    markdown_content[:MAX_CONTENT]
                    + f"\n\n[DOCUMENTO TRUNCADO — {len(markdown_content) - MAX_CONTENT} chars adicionales]"
                )

            return self.extractor(
                markdown_content=markdown_content,
                schema_map=schema_map,
                act_type=act_type,
                legal_context=legal_context,
            )


    class DocumentClassifier(dspy.Module):
        """Clasifica el tipo de acto y roles semánticos de un documento."""

        def __init__(self):
            super().__init__()
            self.classifier = dspy.Predict(DocumentClassifierSignature)

        def forward(self, markdown_content: str) -> dspy.Prediction:
            excerpt = markdown_content[:2000]
            return self.classifier(markdown_excerpt=excerpt)


    class PIIValidator(dspy.Module):
        """Valida campos PII extraídos."""

        def __init__(self):
            super().__init__()
            self.validator = dspy.Predict(PIIValidatorSignature)

        def forward(self, extracted_fields: dict) -> dspy.Prediction:
            # Filtrar solo campos PII para la validación
            pii_fields = {
                k: v for k, v in extracted_fields.items()
                if v and isinstance(v, str) and (
                    len(v) in {12, 13, 18, 20}  # Longitudes típicas de PII
                    or any(kw in k.lower() for kw in ["rfc", "curp", "folio", "nombre"])
                )
            }
            return self.validator(extracted_pii=json.dumps(pii_fields, ensure_ascii=False))


    class ChunkMerger(dspy.Module):
        """Fusiona resultados de chunks múltiples."""

        def __init__(self):
            super().__init__()
            self.merger = dspy.ChainOfThought(ChunkMergerSignature)

        def forward(self, chunk_results: list[dict], schema_map: str) -> dspy.Prediction:
            return self.merger(
                chunk_results=json.dumps(chunk_results, ensure_ascii=False),
                schema_map=schema_map,
            )


# ─────────────────────────────────────────────────────────────────────────────
# 3. PROGRAM PRINCIPAL — Combinación de Módulos
# ─────────────────────────────────────────────────────────────────────────────

if DSPY_AVAILABLE:

    class NotarialExtractionProgram(dspy.Module):
        """
        Programa completo de extracción notarial con DSPy.

        Pipeline:
          1. Clasificar el documento (tipo de acto, roles)
          2. Extraer campos (con o sin chunking)
          3. Validar PII
          4. Retornar JSON listo para mapper.py

        Ejemplo de uso:
          program = NotarialExtractionProgram()
          result = program(
              markdown_content=markdown,
              schema_map=json.dumps(uuid_label_map),
              legal_context="Secciones: Encabezado, Comparecientes...",
          )
          extracted = json.loads(result.final_json)
        """

        def __init__(self, use_cot: bool = True, validate_pii: bool = True):
            super().__init__()
            self.classifier   = DocumentClassifier()
            self.extractor    = NotarialExtractor(use_cot=use_cot)
            self.chunk_merger = ChunkMerger()
            self.pii_validator = PIIValidator() if validate_pii else None
            self.validate_pii  = validate_pii

            # Config
            self.CHUNK_SIZE    = 50_000
            self.CHUNK_OVERLAP = 4_000

        def forward(
            self,
            markdown_content: str,
            schema_map: str,
            legal_context: str = "",
            act_type: str | None = None,
        ) -> dspy.Prediction:

            # ── Paso 1: Clasificar el documento ───────────────────────────────
            if not act_type:
                try:
                    classification = self.classifier(markdown_content)
                    act_type = classification.act_type
                    logger.info(f"[DSPy] Clasificación: {act_type}")
                except Exception as e:
                    logger.warning(f"[DSPy] Clasificación fallida: {e}. Usando 'Acto Notarial'.")
                    act_type = "Acto Notarial"

            # ── Paso 2: Extracción (con chunking si es necesario) ─────────────
            if len(markdown_content) <= self.CHUNK_SIZE:
                # Documento corto — extracción directa
                extraction = self.extractor(
                    markdown_content=markdown_content,
                    schema_map=schema_map,
                    act_type=act_type,
                    legal_context=legal_context,
                )
                raw_json_str = extraction.extracted_json
                chunk_results = None

            else:
                # Documento largo — chunking + merging
                logger.info(f"[DSPy] Documento largo ({len(markdown_content)} chars). Activando chunking.")
                chunks = self._split_chunks(markdown_content)
                chunk_extractions = []

                for i, chunk in enumerate(chunks):
                    logger.info(f"[DSPy] Procesando chunk {i+1}/{len(chunks)}")
                    try:
                        chunk_extraction = self.extractor(
                            markdown_content=chunk,
                            schema_map=schema_map,
                            act_type=act_type,
                            legal_context=legal_context,
                        )
                        chunk_data = self._safe_parse_json(chunk_extraction.extracted_json)
                        chunk_extractions.append(chunk_data)
                    except Exception as e:
                        logger.warning(f"[DSPy] Chunk {i+1} falló: {e}")

                # Fusionar chunks con DSPy
                merge_result = self.chunk_merger(
                    chunk_results=chunk_extractions,
                    schema_map=schema_map,
                )
                raw_json_str = merge_result.merged_json
                chunk_results = chunk_extractions

            # ── Paso 3: Parsear y validar PII ────────────────────────────────
            extracted_fields = self._safe_parse_json(raw_json_str)

            pii_validation = None
            if self.validate_pii and self.pii_validator and extracted_fields:
                try:
                    pii_result = self.pii_validator(extracted_fields)
                    pii_validation = pii_result.validation_result
                    if pii_result.has_critical_errors:
                        logger.warning("[DSPy] ⚠️ PII crítico con errores detectados. Revisar.")
                except Exception as e:
                    logger.warning(f"[DSPy] Validación PII fallida: {e}")

            return dspy.Prediction(
                final_json=json.dumps(extracted_fields, ensure_ascii=False),
                extracted_fields=extracted_fields,
                act_type=act_type,
                pii_validation=pii_validation,
                chunk_results=chunk_results,
            )

        def _split_chunks(self, text: str) -> list[str]:
            """Divide el texto en chunks con overlap."""
            chunks = []
            start = 0
            while start < len(text):
                end = start + self.CHUNK_SIZE
                chunks.append(text[start:end])
                start += self.CHUNK_SIZE - self.CHUNK_OVERLAP
            return chunks

        @staticmethod
        def _safe_parse_json(json_str: str) -> dict:
            """Parsea JSON con tolerancia a errores comunes."""
            import re
            if not json_str:
                return {}
            # Limpiar bloques de código markdown
            cleaned = re.sub(r"```(?:json)?\s*", "", json_str).strip()
            # Extraer objeto JSON
            start = cleaned.find("{")
            end   = cleaned.rfind("}")
            if start == -1 or end == -1:
                return {}
            try:
                return json.loads(cleaned[start:end+1])
            except json.JSONDecodeError:
                try:
                    # Limpiar comentarios y comas finales
                    cleaned2 = re.sub(r"//[^\n]*", "", cleaned[start:end+1])
                    cleaned2 = re.sub(r",\s*([}\]])", r"\1", cleaned2)
                    return json.loads(cleaned2)
                except Exception:
                    return {}


# ─────────────────────────────────────────────────────────────────────────────
# 4. OPTIMIZADORES — Calibración con Corpus Notarial
# ─────────────────────────────────────────────────────────────────────────────

if DSPY_AVAILABLE:

    def load_training_examples(examples_dir: str | None = None) -> list[dspy.Example]:
        """
        Carga ejemplos de entrenamiento desde el directorio de samples.
        Los ejemplos son pares (markdown, expected_json) del corpus RPP.

        Structure esperada en samples/:
          samples/
          ├── bi3_001_input.md      # Markdown del OCR
          ├── bi3_001_expected.json # JSON de extracción correcta
          └── bi20_001_input.md
        """
        root = Path(examples_dir or __file__).parent.parent.parent / "samples"
        examples = []

        if not root.exists():
            logger.warning(f"[DSPy] Directorio de samples no encontrado: {root}")
            return examples

        # Buscar pares input/expected
        for md_file in root.glob("*_input.md"):
            expected_file = md_file.with_name(md_file.stem.replace("_input", "_expected") + ".json")
            if not expected_file.exists():
                continue

            try:
                markdown = md_file.read_text(encoding="utf-8")
                expected = expected_file.read_text(encoding="utf-8")

                # Inferir act_type del nombre del archivo
                act_type = "Compraventa" if "bi3" in md_file.name.lower() else "Acto Notarial"

                ex = dspy.Example(
                    markdown_content=markdown[:50_000],  # Limitar para training
                    schema_map="{}",  # Se inyecta en runtime
                    act_type=act_type,
                    legal_context="",
                    extracted_json=expected,
                ).with_inputs("markdown_content", "schema_map", "act_type", "legal_context")

                examples.append(ex)
            except Exception as e:
                logger.warning(f"[DSPy] Error cargando ejemplo {md_file}: {e}")

        logger.info(f"[DSPy] {len(examples)} ejemplos de entrenamiento cargados.")
        return examples


    def notarial_accuracy_metric(example: dspy.Example, prediction: dspy.Prediction, trace=None) -> float:
        """
        Métrica de evaluación para el optimizador DSPy.
        Compara el JSON extraído con el JSON esperado.

        Retorna score entre 0.0 y 1.0.
        """
        try:
            expected = json.loads(example.extracted_json) if isinstance(example.extracted_json, str) else example.extracted_json
            predicted_str = prediction.extracted_json if hasattr(prediction, "extracted_json") else prediction.final_json
            predicted = json.loads(predicted_str) if isinstance(predicted_str, str) else {}

            if not expected or not predicted:
                return 0.0

            # Normalizar claves (lowercase, strip)
            exp_norm = {str(k).lower().strip(): str(v).strip() for k, v in expected.items() if v}
            pred_norm = {str(k).lower().strip(): str(v).strip() for k, v in predicted.items() if v}

            if not exp_norm:
                return 1.0 if not pred_norm else 0.5

            # Calcular precisión exacta
            correct = sum(
                1 for k, v in exp_norm.items()
                if pred_norm.get(k, "").lower() == v.lower()
            )
            total = len(exp_norm)
            exact_score = correct / total

            # Penalizar alucinaciones (campos en pred que no están en expected)
            hallucinations = sum(
                1 for k in pred_norm
                if k not in exp_norm and pred_norm[k] not in ("null", "", "none")
            )
            hallucination_penalty = min(hallucinations * 0.05, 0.2)

            return max(0.0, exact_score - hallucination_penalty)

        except Exception as e:
            logger.debug(f"[DSPy Metric] Error: {e}")
            return 0.0


    def optimize_extraction_program(
        program: NotarialExtractionProgram,
        examples: list[dspy.Example] | None = None,
        num_threads: int = 4,
        save_path: str | None = None,
    ) -> NotarialExtractionProgram:
        """
        Optimiza el programa de extracción usando BootstrapFewShot.

        Args:
            program: Programa DSPy a optimizar.
            examples: Lista de ejemplos de entrenamiento. Si None, carga desde samples/.
            num_threads: Paralelismo durante la optimización.
            save_path: Si se especifica, guarda el programa optimizado.

        Returns:
            Programa optimizado.
        """
        if examples is None:
            examples = load_training_examples()

        if not examples:
            logger.warning("[DSPy] Sin ejemplos de entrenamiento. Skipping optimización.")
            return program

        logger.info(f"[DSPy] Optimizando con {len(examples)} ejemplos...")

        try:
            from dspy.teleprompt import BootstrapFewShot

            optimizer = BootstrapFewShot(
                metric=notarial_accuracy_metric,
                max_bootstrapped_demos=4,
                max_labeled_demos=4,
                num_threads=num_threads,
            )

            optimized = optimizer.compile(
                program,
                trainset=examples,
            )

            if save_path:
                optimized.save(save_path)
                logger.info(f"[DSPy] Programa optimizado guardado en: {save_path}")

            return optimized

        except Exception as e:
            logger.error(f"[DSPy] Error en optimización: {e}. Retornando programa sin optimizar.")
            return program


# ─────────────────────────────────────────────────────────────────────────────
# 5. CONFIGURACIÓN DE LLM PARA DSPy
# ─────────────────────────────────────────────────────────────────────────────

def configure_dspy_lm(
    provider: str = "google",
    model: str | None = None,
    api_key: str | None = None,
) -> bool:
    """
    Configura el LLM activo para DSPy.
    Debe llamarse antes de usar cualquier módulo DSPy.

    Args:
        provider: "google" | "openai" | "anthropic" | "ollama" | "groq"
        model: Nombre del modelo. Si None, usa el default del proveedor.
        api_key: Si None, busca en variables de entorno.

    Returns:
        True si se configuró correctamente.
    """
    if not DSPY_AVAILABLE:
        logger.warning("[DSPy] DSPy no instalado. No se puede configurar LLM.")
        return False

    try:
        if provider == "google":
            key = api_key or os.environ.get("GOOGLE_API_KEY", "")
            lm = dspy.LM(
                model=f"google/gemini/{model or 'gemini-2.0-flash'}",
                api_key=key,
                max_tokens=8192,
                temperature=0,
            )

        elif provider == "openai":
            key = api_key or os.environ.get("OPENAI_API_KEY", "")
            lm = dspy.LM(
                model=model or "gpt-4o-mini",
                api_key=key,
                max_tokens=8192,
                temperature=0,
            )

        elif provider == "anthropic":
            key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            lm = dspy.LM(
                model=f"anthropic/{model or 'claude-3-5-sonnet-20241022'}",
                api_key=key,
                max_tokens=8192,
                temperature=0,
            )

        elif provider == "groq":
            key = api_key or os.environ.get("GROQ_API_KEY", "")
            lm = dspy.LM(
                model=f"groq/{model or 'llama-3.3-70b-versatile'}",
                api_key=key,
                max_tokens=8192,
                temperature=0,
            )

        elif provider == "ollama":
            base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
            lm = dspy.LM(
                model=f"ollama/{model or 'llama3.1:8b'}",
                api_base=base_url,
                max_tokens=4096,
                temperature=0,
            )

        else:
            logger.error(f"[DSPy] Proveedor no soportado: {provider}")
            return False

        dspy.configure(lm=lm)
        logger.info(f"[DSPy] ✅ LLM configurado: {provider}/{model or 'default'}")
        return True

    except Exception as e:
        logger.error(f"[DSPy] ❌ Error configurando LLM: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 6. FALLBACK — Para cuando DSPy no está disponible
# ─────────────────────────────────────────────────────────────────────────────

class FallbackExtractionWrapper:
    """
    Wrapper que imita la interfaz de NotarialExtractionProgram
    pero usa los prompts tradicionales de agent.py como fallback.
    """

    def __init__(self):
        self.validated = False

    def __call__(
        self,
        markdown_content: str,
        schema_map: str,
        legal_context: str = "",
        act_type: str | None = None,
    ) -> "FallbackResult":
        from engine.agent import extract_form_data
        try:
            schema_dict = json.loads(schema_map) if isinstance(schema_map, str) else schema_map
        except Exception:
            schema_dict = {}

        result = extract_form_data(
            markdown_content=markdown_content,
            json_schema=schema_dict,
            act_id=act_type,
        )
        return FallbackResult(
            final_json=json.dumps(result.get("fields", {}), ensure_ascii=False),
            extracted_fields=result.get("fields", {}),
            act_type=act_type or "unknown",
        )


class FallbackResult:
    def __init__(self, final_json: str, extracted_fields: dict, act_type: str):
        self.final_json       = final_json
        self.extracted_fields = extracted_fields
        self.act_type         = act_type
        self.pii_validation   = None
        self.chunk_results    = None


# ─────────────────────────────────────────────────────────────────────────────
# 7. FACTORY — Crear el programa correcto según disponibilidad
# ─────────────────────────────────────────────────────────────────────────────

def get_extraction_program(
    provider: str = "google",
    model: str | None = None,
    use_cot: bool = True,
    use_dspy: bool | None = None,
) -> "NotarialExtractionProgram | FallbackExtractionWrapper":
    """
    Factory function que retorna el programa de extracción correctó.

    Si DSPy está disponible y se puede configurar → NotarialExtractionProgram.
    Si no → FallbackExtractionWrapper (usa agent.py tradicional).

    Args:
        provider: Proveedor LLM ("google", "groq", "anthropic", etc.)
        model: Modelo específico.
        use_cot: Si True, usa Chain-of-Thought (más preciso, más lento).
        use_dspy: Si None, auto-detecta. Si False, fuerza fallback.

    Returns:
        Programa de extracción listo para usar.
    """
    if use_dspy is False or not DSPY_AVAILABLE:
        logger.info("[DSPy Factory] Usando extractor tradicional (agent.py)")
        return FallbackExtractionWrapper()

    # Configurar LLM para DSPy
    configured = configure_dspy_lm(provider=provider, model=model)
    if not configured:
        logger.warning("[DSPy Factory] Fallback a extractor tradicional.")
        return FallbackExtractionWrapper()

    program = NotarialExtractionProgram(use_cot=use_cot)
    logger.info(f"[DSPy Factory] ✅ NotarialExtractionProgram listo ({provider}/{model or 'default'}, CoT={use_cot})")
    return program


# ─────────────────────────────────────────────────────────────────────────────
# Ejecución directa para pruebas
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    print(f"DSPy disponible: {DSPY_AVAILABLE}")
    if not DSPY_AVAILABLE:
        print("Instalar: pip install dspy-ai")
        sys.exit(1)

    # Demo: cargar ejemplos de entrenamiento
    examples = load_training_examples()
    print(f"Ejemplos de entrenamiento encontrados: {len(examples)}")

    # Demo: crear programa y configurar LLM
    GOOGLE_KEY = os.environ.get("GOOGLE_API_KEY")
    if GOOGLE_KEY:
        program = get_extraction_program(provider="google")
        print(f"Programa listo: {type(program).__name__}")
        print("Para optimizar: optimize_extraction_program(program, examples)")
    else:
        print("GOOGLE_API_KEY no configurada. Usa: export GOOGLE_API_KEY=tu_key")
