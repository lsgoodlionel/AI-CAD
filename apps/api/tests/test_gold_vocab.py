"""生成器发给判读者的 `what` 词表，必须全部被 taxonomy 认得。

生成器扩到六类后，各类的 `what` 词表多出 `oversized`、`sanitary`、`legend`、
`single_line`、`rebar` 等 11 个写法。taxonomy 不认得它们，回收时每一条这样的
裁决都会落进 `unmapped` —— 「未登记的写法会喊」是对的，但喊的应该是判读者
自己编的写法，不该是我们自己发出去的词。

与判据守卫（`test_every_generator_kind_has_real_criteria`）同一个思路：
**把「生成器说的话」与「回收端听得懂的话」锁在一起**，先加类、词表以后再补
就会在这里变红。
"""
from __future__ import annotations

import pytest

from core.model3d.gold import taxonomy as T


def _vocab(spec: dict) -> list[str]:
    return [w.strip() for w in spec["what"].split("/") if w.strip()]


@pytest.mark.unit
def test_every_generator_answer_word_is_known():
    from scripts.model3d.gold_batch import KIND_SPEC

    unknown = {kind: [w for w in _vocab(spec) if T.canonical_what(w) is None]
               for kind, spec in KIND_SPEC.items()}
    unknown = {k: v for k, v in unknown.items() if v}
    assert not unknown, f"taxonomy 不认识生成器自己发出去的词：{unknown}"


@pytest.mark.unit
def test_oversized_is_its_own_bucket():
    """「圈大了」是对象对、范围错 —— 不是别的构件，也不是标注。

    桶按「该由谁去修」分：它要的是边界切分，与构件辨识、图纸级闸、
    标注过滤都不是同一个修法。板第一版 5 块里 4 块错在这里。
    """
    label = T.canonical_what("oversized")
    assert label is not None
    bucket = T.bucket_of(label)
    assert bucket not in ("real_component", "annotation", "drawing_level")
    assert bucket in T.BUCKETS


@pytest.mark.unit
def test_sanitary_is_not_merged_into_equipment():
    """CRITERIA v10 明确「洁具不算设备」—— 并进 furniture_equipment 会抹掉这个决定。"""
    assert T.canonical_what("sanitary") != T.canonical_what("equipment")
