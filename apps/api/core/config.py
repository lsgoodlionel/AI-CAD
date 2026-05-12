from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 数据库
    database_url: str = "postgresql://cad_user:cad_pass@localhost:5432/cad_db"
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "cad_minio"
    minio_secret_key: str = "cad_minio_pass"
    minio_bucket_drawings: str = "drawings"
    minio_bucket_reports: str = "reports"
    minio_use_ssl: bool = False
    # Chroma
    chroma_host: str = "localhost"
    chroma_port: int = 8100
    # JWT
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440           # 24h Access Token
    jwt_refresh_expire_days: int = 30
    # AI 服务
    ai_service_url: str = "http://localhost:8001"
    # 路由器配置
    model_router_cache_ttl_sec: int = 30
    # 企业微信通知（可选）
    wechat_webhook_url: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
