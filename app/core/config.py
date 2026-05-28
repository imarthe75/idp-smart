from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, Union


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Modo de operación ──────────────────────────────────────────────────────
    idp_smart_mode: str = "standalone"
    force_cpu: bool = False  # False = detección automática

    # ── Authentik OIDC ──────────────────────────────────────────────────────────
    authentik_url: str = "https://auth.casmart.internal"
    authentik_client_id: str = "idp-smart"
    authentik_internal_url: str = "http://casmarts-core-authentik-server:9000"

    # ── Base de datos ──────────────────────────────────────────────────────────
    db_host: str = "casmarts-core-db"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: str = "casmarts_secure_pwd" # Ajustar según .env del core
    db_name: str = "postgres"
    valkey_url: str = "redis://casmarts-core-cache:6379/0"

    # ── Almacenamiento (SeaweedFS S3) ──────────────────────────────────────────
    storage_endpoint: str = "casmarts-core-storage:8333"
    storage_access_key: str = "admin"
    storage_secret_key: str = "seaweed_password123"
    storage_bucket: str = "idp-documents"
    storage_secure: bool = False

    # ── Selector de motores ────────────────────────────────────────────────────
    # OCR_ENGINE  : docling | google_doc_ai | aws_textract
    # LLM_PROVIDER: vllm | google | anthropic | openai
    ocr_engine: str = "docling"
    llm_provider: str = "google"

    # ── Smart Router / Cloud Fallback ──────────────────────────────────────────
    enable_cloud_fallback: bool = False
    cloud_fallback_provider: str = "google"
    max_local_queue: int = 5            # umbral de cola para derivar a cloud

    # ── Motor LOCAL (VLLM — Dell / RunPod Pod) ─────────────────────────────────
    local_api_url: str = "http://localhost:8000"
    local_llm_model: str = "granite-3.0-8b-instruct"
    local_llm_timeout: int = 300
    proxy_timeout_s: int = 600          # Agregado para agent.py

    # ── RunPod Lifecycle Management ────────────────────────────────────────────
    runpod_enabled: bool = False
    runpod_api_key: Optional[str] = None
    runpod_pod_docling_id: str = ""     # ID del pod de Docling GPU
    runpod_pod_llm_id: str = ""         # ID del pod de LLM (VLLM)
    runpod_idle_timeout: int = 300      # segundos de inactividad → apagar
    # URLs heredadas (compatibilidad)
    google_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    # ── Docling Remote Server (Microservicio Core) ─────────────────────────────
    docling_server_url: Optional[str] = "http://casmarts-core-docling:5000/extract"
    runpod_docling_url: Optional[str] = None
    runpod_vision_url: Optional[str] = None
    runpod_llm_url: Optional[str] = None
    runpod_timeout: int = 600

    # ── Docling CPU Mode ───────────────────────────────────────────────────────
    docling_chunk_size: Union[int, str] = 10        # páginas por chunk en CPU
    omp_num_threads: int = 0            # 0 = auto (hardware_detector)
    mkl_num_threads: int = 0            # 0 = auto (hardware_detector)

    # ── Google (Gemini) ────────────────────────────────────────────────────────
    google_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.0-flash"
    google_docai_project_id: Optional[str] = None
    google_docai_location: str = "us"
    google_docai_processor_id: Optional[str] = None

    # ── Anthropic (Claude) ─────────────────────────────────────────────────────
    anthropic_api_key: Optional[str] = None
    claude_model: str = "claude-3-5-sonnet-20241022"

    # ── OpenAI / OpenRouter / Together ────────────────────────────────────────
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: Optional[str] = None # Para OpenRouter poner https://openrouter.ai/api/v1

    # ── Groq (Llama-3 / Mixtral) ──────────────────────────────────────────────
    groq_api_key: Optional[str] = None
    groq_model: str = "llama-3.1-70b-versatile"

    # ── Alibaba DashScope (Qwen) ────────────────────────────────────────────────
    alibaba_api_key: Optional[str] = None
    alibaba_model: str = "qwen-plus"

    aws_secret_access_key: Optional[str] = None

    # NVIDIA NIM
    nvidia_nim_api_key: Optional[str] = None
    nvidia_nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_nim_model: str = "ibm/granite-3.0-8b-instruct"

    # ── LocalAI (legacy — compatibilidad) ─────────────────────────────────────
    localai_url: str = "http://localhost:8080/v1"
    model_vision: str = "qwen2-vl"
    model_reasoning: str = "granite-3.0-8b-instruct"
    model_vision_name: str = "nvidia/qwen2-vl-72b-instruct"
    model_reasoning_name: str = "ibm/granite-3.0-8b-instruct"
    localai_temperature: float = 0.1
    localai_max_tokens: int = 2048
    localai_timeout: int = 300

    # ── Vision Optimization (Docling adaptive) ─────────────────────────────────
    vision_detect_scanned_threshold: int = 100
    vision_parallel_workers: int = 1
    vision_use_cache: bool = True
    vision_cache_ttl: int = 604800
    vision_ocr_quality: str = "standard"
    vision_device: str = "auto"
    vision_gpu_layers: int = 0
    vision_allow_gpu: bool = True
    vision_gpu_monitor_interval: int = 5
    vision_gpu_memory_threshold_mb: float = 512.0
    vision_skip_local_ocr: bool = False
    vision_use_cache: bool = True  # Si es True, no usa Docling, solo Multimodal Cloud

    # ── Ensemble (legacy) ─────────────────────────────────────────────────────
    use_ensemble: bool = False
    ensemble_provider: str = "localai"
    ensemble_strategy: str = "sequential"
    ensemble_confidence_threshold: float = 0.7
    qwen_base_url: str = "http://localai:8080/v1"
    qwen_model: str = "qwen2.5:7b"
    qwen_temperature: float = 0.1
    qwen_runpod_endpoint: str = ""
    qwen_runpod_api_key: str = ""

    # ── Compatibilidad RunPod legacy ───────────────────────────────────────────
    llm_runpod_timeout: int = 600
    llm_runpod_model: str = "ibm-granite/granite-3.0-8b-instruct"
    docling_runpod_timeout: int = 600
    docling_runpod_max_retries: int = 3
    docling_runpod_fallback_to_local: bool = True

    # ── Ollama ─────────────────────────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "granite3.1-dense:8b"

    # ── Cloud OCR Configuration ────────────────────────────────────────────────
    # Selector Maestro: docling | google | aws | azure | vision_llm
    ocr_engine: str = "docling"

    # Google Document AI
    google_docai_project_id: str = ""
    google_docai_location: str = "us"
    google_docai_processor_id: str = ""
    
    # AWS Textract
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    
    # ── Vertex AI / Google GenAI SDK ──────────────────────────────────────────
    gcp_project_id: str = "dmc-chorizon"
    gcp_project_id_alt: str = "project-9a4f42ad-5486-49f3-83b"
    gcp_location: str = "us-central1"
    gcp_staging_bucket: Optional[str] = None  # Para PDFs multimodales
    gcp_credentials_json: Optional[str] = "/app/core/credentials/google-v-credentials.json"
    vertex_model: str = "gemini-1.5-flash"

    # ── Celery & Infra ─────────────────────────────────────────────────────────
    worker_concurrency: int = 1

    @property
    def current_llm_model(self) -> str:
        if self.llm_provider == "google":
            return self.gemini_model
        if self.llm_provider == "anthropic":
            return self.claude_model
        if self.llm_provider == "groq":
            return self.groq_model
        if self.llm_provider == "alibaba":
            return self.alibaba_model
        if self.llm_provider == "openai":
            return self.openai_model
        if self.llm_provider == "vertex":
            return self.gemini_model
        if self.llm_provider in ("runpod", "vllm", "local"):
            return self.local_llm_model
        if self.llm_provider == "nvidia":
            return self.nvidia_nim_model
        return "unknown"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()
