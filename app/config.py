from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_gateway"
    encryption_key: str
    pinecone_api_key: str = ""
    pinecone_index_host: str = ""
    rerank_model: str = "bge-reranker-v2-m3"
    embedding_model: str = "nvidia/nv-embedqa-e5-v5"
    embedding_dim: int = 1024
    log_level: str = "INFO"

settings = Settings()