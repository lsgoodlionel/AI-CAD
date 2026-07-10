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
    # ODA File Converter（DWG → DXF 转换，可选；环境变量 ODA_CONVERTER_PATH）
    oda_converter_path: str = ""
    # 路由器配置
    model_router_cache_ttl_sec: int = 30
    # 企业微信通知（可选）
    wechat_webhook_url: str = ""
    # Phase A 灰度特性开关（默认关闭，保证增量上线可逐字节回退到现网行为）
    model_ifc_enabled: bool = False          # 程序化 IFC 建模（FloorElements → 合规 IFC）
    web_fragments_enabled: bool = False      # 前端 That Open Fragments 加载
    vlm_semantic_enabled: bool = False       # VLM 语义读表/判专业微服务

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        # 允许 model_* 字段名（pydantic v2 默认保护 model_ 命名空间）
        protected_namespaces = ()


settings = Settings()
