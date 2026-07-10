"""IFC → That Open Fragments(.frag) 转换封装（Phase A / A-04）。

以子进程调用隔离的 Node 包 ``apps/model-convert/ifc_to_fragments.mjs``
（``@thatopen/fragments`` 的 IfcImporter，web-ifc 解析），把合规 ``.ifc``
转为 Fragments 二进制，供前端 Fragments 加载器高性能渲染。

边界（A-04）：
- 只提供「独立转换 + 上传」两组函数，**不接线进 tasks/model_build.py**
  （那依赖并行的 A-03）。
- 转换失败一律抛 ``FragmentsConversionError``，由上层决定降级（回退
  glTF / 挤出 / 贴图），本模块绝不静默吞错。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from core.storage import upload_file

logger = logging.getLogger(__name__)

# apps/api/services/fragments_convert.py -> 仓库根 -> apps/model-convert
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONVERT_DIR = _REPO_ROOT / "apps" / "model-convert"
_CONVERT_SCRIPT = _CONVERT_DIR / "ifc_to_fragments.mjs"

# 单次转换超时（秒）。1 万构件目标 <30s，留足余量并防止子进程挂死。
_CONVERT_TIMEOUT_SEC = 300

FRAG_CONTENT_TYPE = "application/octet-stream"


class FragmentsConversionError(RuntimeError):
    """IFC → Fragments 转换失败（供上层降级判定）。"""


def _resolve_node_binary() -> str:
    """定位 node 可执行文件（允许 NODE_BINARY 环境变量覆盖）。"""
    node = os.environ.get("NODE_BINARY") or shutil.which("node")
    if not node:
        raise FragmentsConversionError(
            "未找到 node 可执行文件；请安装 Node.js 或设置 NODE_BINARY 环境变量"
        )
    return node


def _ensure_converter_ready() -> None:
    """校验转换脚本与其依赖已就位。"""
    if not _CONVERT_SCRIPT.is_file():
        raise FragmentsConversionError(f"转换脚本缺失：{_CONVERT_SCRIPT}")
    if not (_CONVERT_DIR / "node_modules" / "@thatopen" / "fragments").exists():
        raise FragmentsConversionError(
            f"model-convert 依赖未安装（缺 @thatopen/fragments）；"
            f"请在 {_CONVERT_DIR} 执行 npm install"
        )


def convert_ifc_file_to_fragments(ifc_path: str | os.PathLike[str], frag_path: str | os.PathLike[str]) -> Path:
    """把 IFC 文件转成 Fragments 文件（子进程调用 Node 脚本）。

    Args:
        ifc_path: 输入 ``.ifc`` 路径。
        frag_path: 输出 ``.frag`` 路径。

    Returns:
        写出的 ``.frag`` 路径（Path）。

    Raises:
        FragmentsConversionError: 输入缺失、node/依赖缺失、转换失败或产物为空。
    """
    ifc_path = Path(ifc_path)
    frag_path = Path(frag_path)
    if not ifc_path.is_file():
        raise FragmentsConversionError(f"输入 IFC 不存在：{ifc_path}")

    _ensure_converter_ready()
    node = _resolve_node_binary()

    try:
        proc = subprocess.run(
            [node, str(_CONVERT_SCRIPT), str(ifc_path), str(frag_path)],
            cwd=str(_CONVERT_DIR),
            capture_output=True,
            text=True,
            timeout=_CONVERT_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise FragmentsConversionError(
            f"IFC→Fragments 转换超时（>{_CONVERT_TIMEOUT_SEC}s）：{ifc_path}"
        ) from exc
    except OSError as exc:
        raise FragmentsConversionError(f"启动转换子进程失败：{exc}") from exc

    if proc.returncode != 0:
        raise FragmentsConversionError(
            f"IFC→Fragments 转换失败（exit={proc.returncode}）："
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    if not frag_path.is_file() or frag_path.stat().st_size == 0:
        raise FragmentsConversionError(f"转换未产出有效 .frag：{frag_path}")

    logger.info(
        "IFC→Fragments 成功：%s -> %s (%d bytes)",
        ifc_path,
        frag_path,
        frag_path.stat().st_size,
    )
    return frag_path


def convert_ifc_bytes_to_fragments(ifc_bytes: bytes) -> bytes:
    """把 IFC 字节转成 Fragments 字节（内部走临时文件 + 子进程）。

    Args:
        ifc_bytes: IFC 原始字节。

    Returns:
        Fragments 二进制字节。

    Raises:
        FragmentsConversionError: 输入为空或转换失败。
    """
    if not ifc_bytes:
        raise FragmentsConversionError("输入 IFC 字节为空")

    with tempfile.TemporaryDirectory(prefix="ifc2frag_") as tmp:
        tmp_dir = Path(tmp)
        ifc_path = tmp_dir / "model.ifc"
        frag_path = tmp_dir / "model.frag"
        ifc_path.write_bytes(ifc_bytes)
        convert_ifc_file_to_fragments(ifc_path, frag_path)
        return frag_path.read_bytes()


def fragments_object_key(project_id: int | str, building_key: str) -> str:
    """产物 MinIO key：``projects/{id}/model_ifc/{building_key}.frag``。"""
    return f"projects/{project_id}/model_ifc/{building_key}.frag"


def upload_fragments(frag_bytes: bytes, project_id: int | str, building_key: str) -> str:
    """上传 Fragments 字节到 MinIO，返回 object_key。

    命名沿用现有模型资产惯例（``services/model_builder.py`` 的
    ``projects/{id}/model_assets/...``），Fragments 走独立
    ``model_ifc`` 命名空间与用户上传 IFC 原件区分。

    Raises:
        FragmentsConversionError: 字节为空（避免写出空对象）。
    """
    if not frag_bytes:
        raise FragmentsConversionError("拒绝上传空 Fragments 字节")
    object_key = fragments_object_key(project_id, building_key)
    upload_file(frag_bytes, object_key, FRAG_CONTENT_TYPE)
    logger.info("Fragments 上传完成：%s (%d bytes)", object_key, len(frag_bytes))
    return object_key


def convert_and_upload_ifc_bytes(
    ifc_bytes: bytes, project_id: int | str, building_key: str
) -> tuple[str, int]:
    """一步：IFC 字节 → Fragments → 上传 MinIO。

    Returns:
        (object_key, frag_byte_size)

    Raises:
        FragmentsConversionError: 转换或上传失败。
    """
    frag_bytes = convert_ifc_bytes_to_fragments(ifc_bytes)
    object_key = upload_fragments(frag_bytes, project_id, building_key)
    return object_key, len(frag_bytes)
