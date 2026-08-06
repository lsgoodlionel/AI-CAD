"""按图分组读档案:几何配对不能吃跨图去重后的残片。

**这条防的是我引入的一个真 bug**:`_pairing_z_overrides` 用了
`fetch_project_category`,而它内部 `effective_values` 按
`(category, normalized_key)` 做**全项目**去重,`normalized_key` 里
**不含 drawing_id**:

```python
return f"elevation:{round(float(value_json['elevation_m']), 3)}"
```

于是全项目 6098 条 elevation 里,`16.200` 只留下**一条**(某一张图的),
其余全被去重掉。而楼层名↔标高配对是**同一张图内**的几何操作,
需要那张图完整的标高链 —— 拿到跨图残片必然配不出东西
(实测:2309 张图只配出 1 条)。

去重本身对「项目级信息汇总」是对的(工程信息页要的是「这个项目有哪些标高」),
错的是把它用在几何配对上。**择优要在图内做,不能跨图。**
"""
from __future__ import annotations

import pytest

from services.drawing_archive import group_rows_by_drawing


def _row(did: str, category: str, content: str, *,
         confidence: float = 0.9, source_kind: str = "auto",
         is_active: bool = True) -> dict:
    return {"drawing_id": did, "category": category, "content": content,
            "value_json": None, "confidence": confidence,
            "source_kind": source_kind, "is_active": is_active,
            "supersedes_key": None}


@pytest.mark.unit
def test_same_value_on_different_drawings_is_kept_for_each():
    """核心用例:两张图各有 `16.200`,两张都要留下。"""
    rows = [_row("d1", "elevation", "16.200"),
            _row("d2", "elevation", "16.200")]
    got = group_rows_by_drawing(rows)
    assert set(got) == {"d1", "d2"}
    assert len(got["d1"]) == len(got["d2"]) == 1


@pytest.mark.unit
def test_duplicates_within_one_drawing_are_still_deduped():
    """图内择优照旧 —— 同一张图上同一个值只留置信度最高的那条。"""
    rows = [_row("d1", "elevation", "16.200", confidence=0.5),
            _row("d1", "elevation", "16.200", confidence=0.95)]
    got = group_rows_by_drawing(rows)
    assert len(got["d1"]) == 1
    assert got["d1"][0]["confidence"] == pytest.approx(0.95)


@pytest.mark.unit
def test_inactive_rows_are_dropped():
    rows = [_row("d1", "elevation", "16.200", is_active=False)]
    assert group_rows_by_drawing(rows) == {}


@pytest.mark.unit
def test_verified_wins_over_auto_within_a_drawing():
    rows = [_row("d1", "elevation", "16.200", confidence=0.99),
            _row("d1", "elevation", "16.200", source_kind="verified",
                 confidence=0.1)]
    got = group_rows_by_drawing(rows)
    assert got["d1"][0]["source_kind"] == "verified"


@pytest.mark.unit
def test_a_full_elevation_chain_survives():
    """整条标高链要完整保留 —— 这是配对的输入。"""
    rows = [_row("d1", "elevation", v) for v in
            ("±0.000", "5.400", "10.800", "16.200", "21.890")]
    assert len(group_rows_by_drawing(rows)["d1"]) == 5


@pytest.mark.unit
def test_empty_input():
    assert group_rows_by_drawing([]) == {}
