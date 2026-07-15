"""远程 VLM 读图适配器（qwen3.5-vision，经 Ollama ``/api/chat``）。

语义候选源，不是权威真值：判专业 + 读标高 + 识构件，均带置信度，供人工/
规则复核后再采信。绝不产出计数/坐标/尺寸/QTO——那些留给确定性几何管线
（``core/model3d/topology_rules.py``、``services/model_qto.py`` 等）。

端点从环境变量或 DB ``llm_providers`` 配置读取，绝不硬编码，见
``ollama_vlm.resolve_base_url``。用法与能力边界见 ``docs/PHASE_D_VLM_READ.md``。
"""
from __future__ import annotations

from .ollama_vlm import DEFAULT_MODEL, call_vlm_chat, prepare_image, read_drawing_vlm, resolve_base_url
from .parse import parse_components, parse_discipline, parse_elevations, parse_vlm_text
from .types import ComponentCandidate, DisciplineCandidate, ElevationCandidate, VlmReadResult

__all__ = [
    "ComponentCandidate",
    "DEFAULT_MODEL",
    "DisciplineCandidate",
    "ElevationCandidate",
    "VlmReadResult",
    "call_vlm_chat",
    "parse_components",
    "parse_discipline",
    "parse_elevations",
    "parse_vlm_text",
    "prepare_image",
    "read_drawing_vlm",
    "resolve_base_url",
]
