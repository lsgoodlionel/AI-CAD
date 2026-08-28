"""批次编号生成 —— 判读接触表上的四位随机码。

**为什么要随机码**：可预测的 `L1-01` 编号让判读者直接重放上一批的答案
（实测两次不同的图得到逐格相同的 103 条回答）。随机码让重放在结构上
无法提交 —— 重放会带着上一批的编号，一眼可见。

**为什么剔除成对的易混字符**：实测三次转写错误，一次比一次说明问题 ——

    YIWX → Y1WX   大写 I 读成数字 1
    7N2M → 7NZM   数字 2 读成**已被剔除的 Z**
    WY7F → WYTF   数字 7 读成字母 T

第二例是关键：剔除 Z 完全没用，判读者会把保留的 `2` 读成 `Z`。
易混是**成对**的属性，必须两个都剔。这与 GB/T 50001 §8.0.4
「轴号不得用 I、O、Z」同源 —— 易混字符不该进标识符。
"""
from __future__ import annotations

import random
import string

#: 成对剔除后的字母表。剔掉 I1 O0 S5 Z2 T7 B8 G6 D Q。
CODE_ALPHABET = "".join(
    c for c in string.ascii_uppercase + string.digits
    if c not in "I1O0S5Z2T7B8G6DQ"
)


def make_codes(count: int, *, seed: int) -> list[str]:
    """生成 ``count`` 个互不相同的四位编号，顺序已打乱。

    同一 ``seed`` 给出同一批编号，便于复现某一次批次。
    """
    rng = random.Random(seed)
    seen: set[str] = set()
    while len(seen) < count:
        seen.add("".join(rng.choice(CODE_ALPHABET) for _ in range(4)))
    codes = sorted(seen)
    rng.shuffle(codes)
    return codes
