#!/usr/bin/env python3
"""远程 VLM 语义读图 CLI（人工真跑用；本模块自动化测试全 mock，不联网）。

对本地图纸文件（PDF 首页或位图）跑远程 qwen3.5-vision 读图，输出判专业 +
标高候选 + 构件候选（均为语义候选，非权威真值，见
``core/model3d/vlm_read/types.py`` 铁律）。

端点从环境变量 ``REMOTE_OLLAMA_BASE_URL`` 或 DB ``llm_providers``『Ollama 远程』
读取，本脚本不接受、也不应硬编码真实地址；可用 ``--base-url`` 显式覆盖
（仅供本地临时调试，不要把真实地址写进任何被提交的文件/脚本参数默认值）。

用法：
  python scripts/model3d/vlm_read_drawing.py <图纸.pdf|.png> [--dpi 150]
  REMOTE_OLLAMA_BASE_URL=http://<host>:11434 python scripts/model3d/vlm_read_drawing.py <图纸.pdf>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 允许从 apps/api 根导入
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.model3d.vlm_read import DEFAULT_MODEL, read_drawing_vlm  # noqa: E402

# CLI 渲染 PDF 首页用的默认 dpi：VLM 只需看清标高/构件标注文字，无需高精度，
# 且 read_drawing_vlm 内部还会二次缩放到 ~1280px 宽，这里给个够用的中等值即可
_DEFAULT_RENDER_DPI = 150


def _load_image_bytes(path: Path, dpi: int) -> bytes:
    """PDF 首页 → PNG 字节；位图文件原样读取（缩放交给 read_drawing_vlm）。"""
    ext = path.suffix.lower().lstrip(".")
    data = path.read_bytes()
    if ext != "pdf" and data[:5] != b"%PDF-":
        return data

    import fitz  # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    if doc.page_count == 0:
        raise ValueError("PDF 无页面")
    pix = doc[0].get_pixmap(dpi=dpi)
    return pix.tobytes("png")


async def _run(path: Path, dpi: int, base_url: str | None, model: str) -> int:
    try:
        image_bytes = _load_image_bytes(path, dpi)
    except Exception as exc:  # noqa: BLE001
        print(f"渲染失败: {exc}", file=sys.stderr)
        return 2

    result = await read_drawing_vlm(image_bytes, base_url=base_url, model=model)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if not result.available:
        print(
            "\n提示: 未获得 VLM 读图结果。检查 REMOTE_OLLAMA_BASE_URL 是否已设置，"
            "或 DB llm_providers 是否已配置『Ollama 远程』。",
            file=sys.stderr,
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="远程 VLM 语义读图（判专业/标高候选/构件候选）")
    parser.add_argument("path", help="图纸文件路径（pdf/png/jpg）")
    parser.add_argument("--dpi", type=int, default=_DEFAULT_RENDER_DPI, help="PDF 渲染 dpi（仅 PDF 输入生效）")
    parser.add_argument(
        "--base-url",
        default=None,
        help="临时覆盖远程 VLM 端点（调试用；不要把真实地址写进被提交的文件）",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama 模型名（默认 {DEFAULT_MODEL}）")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 2

    return asyncio.run(_run(path, args.dpi, args.base_url, args.model))


if __name__ == "__main__":
    raise SystemExit(main())
