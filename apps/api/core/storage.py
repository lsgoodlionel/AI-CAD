"""MinIO 对象存储客户端封装"""
import io
from minio import Minio
from minio.error import S3Error
from core.config import settings

_client: Minio | None = None
_public_client: Minio | None = None


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


def _get_presign_client() -> Minio:
    """用于生成预签名 URL 的客户端。

    优先用 minio_public_endpoint(浏览器可达),使签名 host 与浏览器访问一致,
    避免内网 endpoint(minio:9000)导致的 ERR_NAME_NOT_RESOLVED。仅本地签名,
    不发起网络连接;未配置公网端点时回退内网客户端(行为不变)。
    """
    global _public_client
    if not settings.minio_public_endpoint:
        return get_minio()
    if _public_client is None:
        _public_client = Minio(
            settings.minio_public_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_use_ssl,
            # 显式 region:预签名仅本地签名,公网端点在容器内不可达,
            # 设 region 可避免 minio-py 触发 GetBucketLocation 网络查询。
            region="us-east-1",
        )
    return _public_client


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
    """生成签名下载 URL（默认 5 分钟有效）。用浏览器可达端点签名（见 _get_presign_client）。"""
    from datetime import timedelta
    bucket = bucket or settings.minio_bucket_drawings
    client = _get_presign_client()
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


def object_exists(object_key: str, bucket: str | None = None) -> bool:
    """对象是否存在（stat 探测；网络/权限异常一律视为不存在,由调用方降级）。"""
    bucket = bucket or settings.minio_bucket_drawings
    try:
        get_minio().stat_object(bucket, object_key)
        return True
    except Exception:  # noqa: BLE001 — S3Error/网络错误统一按 miss 处理
        return False
