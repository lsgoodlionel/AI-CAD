"""GB 55xxx 通用规范：**全部条文均为强制性条文**。

**实测**（防水通用规范 GB 55030-2022 入库后）：

    159 条 → is_mandatory 仅 72 条、MUST 仅 1 条

而住建部对**工程建设强制性国家标准**（GB 55001~55037「通用规范」系列）
的规定是：**全部条文必须严格执行**。判定漏掉了规范本身的性质：

    4.6.8 采用整体装配式卫浴间的结构楼地面应采取防排水措施。
    → 判为 SHOULD / 非强条   ← **错**，它同样是强条

义务词（应/不应/宜）区分的是**要求的严格程度**，
而强制性由**规范类型**决定 —— 两者是不同的维度，此前混为一谈。
"""
from __future__ import annotations

import pytest

from services.regulation_importer import is_mandatory_standard


@pytest.mark.unit
def test_general_codes_are_mandatory():
    """GB 55xxx「通用规范」系列 —— 全部条文强制。"""
    for title in ("GB 55008-2021《混凝土结构通用规范》",
                  "《建筑防火通用规范》GB 55037-2022",
                  "GB55030-2022《建筑与市政工程防水通用规范》.pdf",
                  "GB 55001-2021 工程结构通用规范"):
        assert is_mandatory_standard(title), title


@pytest.mark.unit
def test_ordinary_codes_are_not_blanket_mandatory():
    """**普通规范不是全文强制** —— 它们只有黑体条文是强条。"""
    for title in ("GB 50010-2010《混凝土结构设计规范》",
                  "JGJ 3-2010 高层建筑混凝土结构技术规程",
                  "GB/T 50001-2017 房屋建筑制图统一标准"):
        assert not is_mandatory_standard(title), title


@pytest.mark.unit
def test_recognises_by_number_or_name():
    """编号(GB 55xxx)或名称(通用规范)任一命中即可 ——
    实测文件名格式不统一,两种写法都有。"""
    assert is_mandatory_standard("GB 55023-2022")          # 只有编号
    assert is_mandatory_standard("施工脚手架通用规范")      # 只有名称


@pytest.mark.unit
def test_empty_is_not_mandatory():
    """**判不出就不标强条** —— 误标强条会让审图误报。"""
    assert not is_mandatory_standard("")
    assert not is_mandatory_standard(None)
    assert not is_mandatory_standard("某某技术手册")
