"""判读者健全性闸：一批判读结果在进金标准之前，先证明它是「看过图」的。

实测触发：图层分类器那一批，**两次不同的图**得到**逐格相同的 103 条回答**
（100/100 重合，最高频答案占 69%，confident 全 true，note 全空）。
判读者重放了上一次的答案。若无此闸，这批会安静地变成金标准。

四条判据全部取在**退化的极端**上，不设可调阈值。
"""
import pytest

from core.model3d.gold.judge_sanity import check_batch


def _batch(answers, confident=True, notes=""):
    return [{"id": f"T-{i:02d}", "what": a, "confident": confident, "note": notes}
            for i, a in enumerate(answers, 1)]


def test_干净的一批不报问题():
    b = _batch(["column", "beam", "wall", "door", "axis", "pipe"])
    b[0]["confident"] = False
    b[1]["note"] = "只看到一段尺寸线"
    assert check_batch(b) == []


def test_与上一批逐格相同判为重放():
    prev = _batch(["column", "beam", "wall", "door"])
    cur = _batch(["column", "beam", "wall", "door"])
    cur[0]["confident"] = False
    cur[1]["note"] = "x"
    issues = check_batch(cur, prior=prev)
    assert any(i.kind == "replay" for i in issues)


def test_只要有一格不同就不算重放():
    prev = _batch(["column", "beam", "wall", "door"])
    cur = _batch(["column", "beam", "wall", "slab"])
    cur[0]["confident"] = False
    cur[1]["note"] = "x"
    assert not any(i.kind == "replay" for i in check_batch(cur, prior=prev))


def test_没有一格标不确定且没有一条备注判为退化():
    b = _batch(["column", "beam", "wall"] * 12)          # 36 格
    issues = check_batch(b)
    assert any(i.kind == "no_uncertainty" for i in issues)


def test_格数太少时不判退化():
    """三五格全有把握是正常的，不该报警。"""
    assert check_batch(_batch(["column", "beam", "wall"])) == []


def test_每个分层的众数都一样判为没看分层():
    """分层抽样时，若各层给出同一个众数答案，说明答案与图无关。"""
    b = _batch(["beam"] * 9)
    strata = {f"T-{i:02d}": s for i, s in enumerate(
        ["axis"] * 3 + ["column"] * 3 + ["door"] * 3, 1)}
    b[0]["confident"] = False
    b[1]["note"] = "x"
    assert any(i.kind == "stratum_blind" for i in check_batch(b, strata=strata))


def test_分层各有各的众数时不报():
    b = _batch(["axis", "axis", "beam", "column", "column", "beam",
                "door", "door", "beam"])
    strata = {f"T-{i:02d}": s for i, s in enumerate(
        ["axis"] * 3 + ["column"] * 3 + ["door"] * 3, 1)}
    b[0]["confident"] = False
    b[1]["note"] = "x"
    assert not any(i.kind == "stratum_blind" for i in check_batch(b, strata=strata))


def test_单一分层不判分层盲():
    b = _batch(["beam"] * 9)
    strata = {f"T-{i:02d}": "beam" for i in range(1, 10)}
    b[0]["confident"] = False
    b[1]["note"] = "x"
    assert not any(i.kind == "stratum_blind" for i in check_batch(b, strata=strata))


def test_问题带得上人话说明():
    b = _batch(["beam"] * 40)
    for i in check_batch(b):
        assert i.detail and not i.detail.endswith(("。", "."))  # 一句短话，不带句号
