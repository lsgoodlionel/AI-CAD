"""MinIO 对象存储客户端封装"""
import io
from minio import Minio
from minio.error import S3Error
from core.config import settings

_client: Minio | None = None


def get_minio() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_use_ssl,
        )
        _ensure_buckets(_client)
    return _client


def _ensure_buckets(client: Minio) -> None:
    for bucket in (settings.minio_bucket_drawings, settings.minio_bucket_reports):
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)


def upload_file(
    data: bytes,
    object_key: str,
    content_type: str = "application/octet-stream",
    bucket: str | None = None,
) -> str:
    """上传文件，返回 object_key"""
    bucket = bucket or settings.minio_bucket_drawings
    client = get_minio()
    client.put_object(
        bucket,
        object_key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return object_key


def presigned_get_url(object_key: str, expires_seconds: int = 300, bucket: str | None = None) -> str:
    """生成签名下载 URL（默认 5 分钟有效）"""
    from datetime import timedelta
    bucket = bucket or settings.minio_bucket_drawings
    client = get_minio()
    return client.presigned_get_object(bucket, object_key, expires=timedelta(seconds=expires_seconds))


def get_file_bytes(object_key: str, bucket: str | None = None) -> bytes:
    """从 MinIO 下载文件，返回原始字节"""
    bucket = bucket or settings.minio_bucket_drawings
    client = get_minio()
    response = client.get_object(bucket, object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def delete_object(object_key: str, bucket: str | None = None) -> None:
    bucket = bucket or settings.minio_bucket_drawings
    get_minio().remove_object(bucket, object_key)
