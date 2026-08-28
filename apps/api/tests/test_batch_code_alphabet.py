"""批次编号字母表：剔除**成对**的易混字符，不是剔除其中一个。

实测三次转写错误，一次比一次说明问题：

    YIWX → Y1WX   大写 I 读成数字 1   （于是剔除了 I 和 1）
    L96T → L967T  多打一个 7
    7N2M → 7NZM   数字 2 读成 **已被剔除的 Z**
    WY7F → WYTF   数字 7 读成字母 T

第三例是关键：**剔除 Z 完全没用** —— 判读者会把保留的 `2` 读成 `Z`。
易混是**成对**的属性，必须两个都剔。
"""
from core.model3d.gold.batch_codes import CODE_ALPHABET, make_codes

# 实测撞见过的易混对（两侧都必须不在字母表里）
CONFUSABLE_PAIRS = (("I", "1"), ("O", "0"), ("S", "5"), ("Z", "2"), ("T", "7"),
                    ("B", "8"), ("G", "6"), ("D", "0"), ("Q", "O"))


def test_每一对易混字符都不出现():
    for a, b in CONFUSABLE_PAIRS:
        assert a not in CODE_ALPHABET, f"{a} 与 {b} 易混，应两个都剔"
        assert b not in CODE_ALPHABET, f"{b} 与 {a} 易混，应两个都剔"


def test_字母表够用():
    """四位编码要能覆盖单批最大规模（实测最大 103 格）并留出余量。"""
    assert len(CODE_ALPHABET) ** 4 > 100_000


def test_生成的编号不重复且都来自字母表():
    codes = make_codes(200, seed=1)
    assert len(codes) == len(set(codes)) == 200
    assert all(len(c) == 4 and set(c) <= set(CODE_ALPHABET) for c in codes)


def test_同一个种子给出同一批编号():
    assert make_codes(50, seed=7) == make_codes(50, seed=7)


def test_不同种子给出不同批编号():
    assert make_codes(50, seed=7) != make_codes(50, seed=8)
