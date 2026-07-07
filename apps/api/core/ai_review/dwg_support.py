"""
DWG → DXF 转换支持（蓝图 4.3）

ezdxf 不支持二进制 DWG；本模块通过 ODA File Converter
（settings.oda_converter_path，环境变量 ODA_CONVERTER_PATH）将 DWG 转为 DXF
后再交给 ezdxf 解析。未配置 / 转换失败时返回 warning 文本，由 vision_engine
转为 INFO 级问题提示，不再盲目 ezdxf.read 报错。
"""
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from core.config import settings

logger = logging.getLogger(__name__)

DWG_MAGIC_PREFIX = b"AC10"   # DWG 文件魔数（AC1015/AC1027/AC1032 等版本号前缀）
ODA_MISSING_WARNING = (
    "DWG 为二进制格式无法直接解析：需安装 ODA File Converter"
    "（配置环境变量 ODA_CONVERTER_PATH）或 GNU LibreDWG（dwg2dxf），"
    "或上传 DXF/PDF 版本图纸"
)
_DWG2DXF_TIMEOUT_SEC = 60


def ensure_dxf(data: bytes, file_ext: str) -> tuple[bytes, str, str | None]:
    """DWG → DXF 转换入口。返回 (data, effective_ext, warning)。

    - file_ext != 'dwg' → 原样透传
    - 无 DWG 魔数 → 疑似 DXF 文本误存为 .dwg，按 dxf 处理
    - 魔数命中 → 依次尝试 ODA File Converter（ODA_CONVERTER_PATH）
      → GNU LibreDWG dwg2dxf（PATH 自动发现）；均不可用/失败 → warning 降级
    """
    if file_ext != "dwg":
        return data, file_ext, None
    if not data.startswith(DWG_MAGIC_PREFIX):
        return data, "dxf", None

    if settings.oda_converter_path:
        try:
            return _convert_with_oda(data), "dxf", None
        except Exception as exc:  # noqa: BLE001 — ODA 失败继续尝试 LibreDWG
            logger.warning("[DWGSupport] ODA 转换失败，尝试 LibreDWG: %s", exc)

    dwg2dxf = shutil.which("dwg2dxf")
    if dwg2dxf:
        try:
            return _convert_with_dwg2dxf(dwg2dxf, data), "dxf", None
        except Exception as exc:  # noqa: BLE001 — 转换失败降级为提示而非中断审图
            logger.warning("[DWGSupport] dwg2dxf 转换失败: %s", exc)
            return data, "dwg", (
                f"DWG 转换失败（{exc}），请检查转换器配置或上传 DXF/PDF 版本图纸"
            )
    return data, "dwg", ODA_MISSING_WARNING


def _convert_with_dwg2dxf(executable: str, data: bytes) -> bytes:
    """经临时文件调用 GNU LibreDWG dwg2dxf 完成 DWG → DXF 转换"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        dwg_path = Path(tmp_dir) / "drawing.dwg"
        dxf_path = Path(tmp_dir) / "drawing.dxf"
        dwg_path.write_bytes(data)
        subprocess.run(
            [executable, "-o", str(dxf_path), str(dwg_path)],
            check=True, capture_output=True, timeout=_DWG2DXF_TIMEOUT_SEC,
        )
        if not dxf_path.exists() or dxf_path.stat().st_size == 0:
            raise RuntimeError("dwg2dxf 未产出有效 DXF")
        return dxf_path.read_bytes()


def _convert_with_oda(data: bytes) -> bytes:
    """经临时文件调用 ezdxf.addons.odafc 完成 DWG → DXF 转换"""
    from ezdxf.addons import odafc

    for attr in ("win_exec_path", "unix_exec_path", "exec_path"):
        if hasattr(odafc, attr):
            setattr(odafc, attr, settings.oda_converter_path)
    with tempfile.TemporaryDirectory() as tmp_dir:
        dwg_path = Path(tmp_dir) / "drawing.dwg"
        dxf_path = Path(tmp_dir) / "drawing.dxf"
        dwg_path.write_bytes(data)
        odafc.convert(str(dwg_path), str(dxf_path), replace=True)
        return dxf_path.read_bytes()
