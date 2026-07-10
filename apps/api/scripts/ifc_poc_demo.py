"""IfcOpenShell 程序化建模 PoC —— 合成样例 → /tmp/model_poc.ifc。

运行：
    cd apps/api && python scripts/ifc_poc_demo.py

前置：已安装 ifcopenshell（见 requirements.txt）。产出一份 2 层单体的 IFC4 文件，
含若干柱/墙/板/梁，并打印各类构件统计。
"""
from __future__ import annotations

import os
import sys

# 让脚本可直接从 apps/api 根目录运行（补齐 import 路径）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.model3d.types import FloorElements  # noqa: E402
from services.model_ifc_builder import (  # noqa: E402
    IfcBuildingInput,
    IfcProjectInput,
    IfcStoryInput,
    build_ifc_from_model,
)

OUTPUT_PATH = "/tmp/model_poc.ifc"

# 8.4m × 8.4m 柱网的一层合成构件（米坐标，原点在最小轴号交点）。
_COLUMNS = [
    {"outline": [[x - 0.3, y - 0.3], [x + 0.3, y - 0.3], [x + 0.3, y + 0.3], [x - 0.3, y + 0.3]], "src": "demo"}
    for x in (0.0, 8.4)
    for y in (0.0, 8.4)
]
_WALLS = [
    {"path": [[0.0, 0.0], [8.4, 0.0]], "width": 0.2, "src": "demo"},
    {"path": [[0.0, 0.0], [0.0, 8.4]], "width": 0.2, "src": "demo"},
]
_SLABS = [
    {"outline": [[0.0, 0.0], [8.4, 0.0], [8.4, 8.4], [0.0, 8.4]], "thickness": 0.12, "src": "demo"},
]
_BEAMS = [
    {"path": [[0.0, 0.0], [8.4, 0.0]], "width": 0.3, "depth": 0.6, "src": "demo"},
]


def _floor_elements() -> FloorElements:
    return FloorElements(
        scale=0.0352778,
        axes={"x": [["1", 0.0], ["2", 8.4]], "y": [["A", 0.0], ["B", 8.4]], "elevations": []},
        columns=list(_COLUMNS),
        walls=list(_WALLS),
        beams=list(_BEAMS),
        slabs=list(_SLABS),
    )


def _project_input() -> IfcProjectInput:
    stories = tuple(
        IfcStoryInput(
            story_key=f"F{level}",
            display_name=f"{level}层",
            story_order=level,
            elevation_m=round((level - 1) * 4.5, 3),
            height_m=4.5,
            elements=_floor_elements(),
        )
        for level in (1, 2)
    )
    building = IfcBuildingInput(unit_key="main", display_name="示例单体", stories=stories)
    return IfcProjectInput(project_name="PoC 示例工程", buildings=(building,))


def main() -> None:
    result = build_ifc_from_model(_project_input(), output_path=OUTPUT_PATH)
    print(f"IFC 已写入: {result.path}")
    print("构件统计:")
    for kind, count in sorted(result.counts.items()):
        print(f"  {kind:<10} {count}")
    print(f"总计: {sum(result.counts.values())} 个构件")


if __name__ == "__main__":
    main()
