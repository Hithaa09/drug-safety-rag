"""Settings for Person B's modules, read once from environment variables (or an untracked .env).

No module reads os.environ directly; call get_settings() instead. Variable names match the spec
(p20), so these merge into the main repo's config without renaming.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())

    # PostgreSQL connection string (documents and evidence_chunks live in the kb schema).
    database_url: str = "postgresql://drugsafety:drugsafety@localhost:5432/drugsafety"
    # Folder for raw source files, caches and run outputs.
    data_dir: Path = Path("data")
    # OpenAI-compatible endpoint of the model server (vLLM on the GPU workbench).
    model_url: str = "http://localhost:8000/v1"
    # Model name sent to that endpoint.
    model_name: str = "Qwen/Qwen2.5-7B-Instruct"
    # API key the model server requires (vLLM --api-key); "local" when it requires none.
    model_api_key: str = "local"
    # True: ask vLLM to constrain output to the Report JSON schema.
    model_guided_json: bool = False
    # Longest answer, in tokens, and how long to wait for it.
    model_max_tokens: int = 1500
    model_timeout_s: float = 180.0
    # Evidence budget per patient (BGE tokens) when assembling the LLM context.
    llm_context_tokens: int = 3500
    # Embedding model; must match the vectors stored in kb.evidence_chunks.
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    # Where the Streamlit interface finds Person A's API.
    api_url: str = "http://localhost:8080"
    # How long the interface waits for one assessment (two model calls plus retrieval).
    api_timeout_s: float = 420.0
    # Logging verbosity.
    log_level: str = "INFO"
    # Frozen v1 drug and gene list.
    scope_file: Path = Path("scope/v1.yaml")
    # Optional keys that raise rate limits (NCBI: 3 -> 10 requests/s).
    ncbi_api_key: str | None = None
    openfda_api_key: str | None = None

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
