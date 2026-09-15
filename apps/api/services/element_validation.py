"""没通过金标准的构件类 —— 模型页默认隐藏并标「未验证」。

写死而不是运行时读金标准目录：API 不该依赖一堆判读文件在不在。
一致性由 `tests/test_element_validation.py` 对着金标准钉住 —— 某天管线或设备
判对了，那条测试会提醒来改这里。
"""
from __future__ import annotations

#: 构件类 → 为什么没通过（给用户看的一句话，带金标准出处）。
UNVERIFIED_ELEMENT_KINDS: dict[str, str] = {
    "pipes": ("金标准两批 0/58、0/16 判对：识别出的「管线」多是机电图下面"
              "建筑底图里的结构线（pipes_v1、pipes_pipe3）"),
    "equipment": ("金标准 0/16 判对：框中的是座椅、家具、单线与文字"
                  "（equipment_equip3）"),
}


def element_validation() -> dict:
    """GET /model 附带的构件可信度说明（与 scene 版本无关，回滚到旧版本也有）。"""
    return {"unverified": [{"kind": kind, "reason": reason}
                           for kind, reason in UNVERIFIED_ELEMENT_KINDS.items()]}
