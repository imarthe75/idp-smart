from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Cloud-Ready & Standalone Mode
    idp_smart_mode: str = "standalone"
    force_cpu: bool = True

    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: str = ""
    db_name: str = "postgres"
    valkey_url: str = "redis://localhost:6379/0" 
    
    # Minio Configuration
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minio_user"
    minio_secret_key: str = "minio_password"
    minio_bucket: str = "idp-documents"
    minio_secure: bool = False

    # Pipeline de Inferencia Especializado (LocalAI)
    localai_url: str = "http://localhost:8080/v1"
    model_vision: str = "qwen2-vl"
    model_reasoning: str = "granite-3.0-8b-instruct"
    localai_temperature: float = 0.1
    localai_max_tokens: int = 2048
    localai_timeout: int = 300
    
    # === VISION OPTIMIZATION (Docling) - CPU/GPU/RunPod Adaptive ===
    vision_detect_scanned_threshold: int = 100
    vision_parallel_workers: int = 4
    vision_use_cache: bool = True
    vision_cache_ttl: int = 604800
    vision_ocr_quality: str = "standard"
    
    vision_allow_gpu: bool = True
    vision_gpu_monitor_interval: int = 5
    vision_gpu_memory_threshold_mb: float = 512.0
    
    # RunPod Serverless para Docling
    docling_runpod_enabled: bool = False
    docling_runpod_endpoint: str = ""
    docling_runpod_api_key: str = ""
    docling_runpod_timeout: int = 300
    docling_runpod_max_retries: int = 3
    docling_runpod_batch_size: int = 4
    docling_runpod_fallback_to_local: bool = True
    
    vision_device: str = "auto"
    vision_gpu_layers: int = 0

    llm_provider: str = "localai"
    use_ensemble: bool = False

    # === RUNPOD SERVERLESS PARA LLM (Razonamiento) ===
    llm_runpod_enabled: bool = False
    llm_runpod_endpoint: str = ""
    llm_runpod_api_key: str = ""
    llm_runpod_timeout: int = 600
    llm_runpod_model: str = "granite-3.0-8b-instruct"
    
    # === GOOGLE GEMINI (Para validación externa) ===
    google_api_key: str = ""
    
    # === QWEN / RUNPOD (Ensemble) ===
    qwen_base_url: str = "http://localai:8080/v1"
    qwen_model: str = "qwen2.5:7b"
    qwen_temperature: float = 0.1
    qwen_runpod_endpoint: str = ""
    qwen_runpod_api_key: str = ""
    ensemble_provider: str = "localai"
    ensemble_strategy: str = "sequential"
    ensemble_confidence_threshold: float = 0.7

    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

settings = Settings()
