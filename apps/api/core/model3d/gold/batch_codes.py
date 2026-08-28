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

**但剔除清单追不上。** 成对剔除之后又撞见两对新的：

    JU9N → JUGN   数字 9 读成字母 G（G 已剔，9 还在）
    YMFF → YMMF   字母 F 读成字母 M

所以第四位是**校验位**：任何单字符转写错误都能被检出，
并在候选唯一时自动纠正（`repair_code`）。这比继续扩充剔除清单可靠 ——
清单靠猜，校验位靠算。
"""
from __future__ import annotations

import random
import string

#: 成对剔除后的字母表。剔掉 I1 O0 S5 Z2 T7 B8 G6 D Q。
CODE_ALPHABET = "".join(
    c for c in string.ascii_uppercase + string.digits
    if c not in "I1O0S5Z2T7B8G6DQ"
)


def _check_char(payload: str) -> str:
    """三位载荷 → 一位校验字符（各位在字母表中的序号求和取模）。"""
    total = sum(CODE_ALPHABET.index(c) for c in payload)
    return CODE_ALPHABET[total % len(CODE_ALPHABET)]


def is_valid_code(code: str) -> bool:
    """校验位对不对。字符不在字母表里、长度不对，一律 False。"""
    if len(code) != 4 or any(c not in CODE_ALPHABET for c in code):
        return False
    return code[3] == _check_char(code[:3])


def repair_code(code: str, issued: set[str] | None = None) -> str | None:
    """尝试纠正一处转写错误；候选不唯一或无解时返回 ``None``。

    ``issued`` 是本批实际发出的编号集合。给了它就只在集合内找候选，
    纠正率更高、误纠风险更低。
    """
    if is_valid_code(code) and (issued is None or code in issued):
        return code
    if len(code) != 4:
        return None
    cands = set()
    for i in range(4):
        for c in CODE_ALPHABET:
            alt = code[:i] + c + code[i + 1:]
            if is_valid_code(alt) and (issued is None or alt in issued):
                cands.add(alt)
    return cands.pop() if len(cands) == 1 else None


def make_codes(count: int, *, seed: int) -> list[str]:
    """生成 ``count`` 个互不相同的四位编号（末位为校验位），顺序已打乱。

    同一 ``seed`` 给出同一批编号，便于复现某一次批次。
    """
    rng = random.Random(seed)
    seen: set[str] = set()
    while len(seen) < count:
        payload = "".join(rng.choice(CODE_ALPHABET) for _ in range(3))
        seen.add(payload + _check_char(payload))
    codes = sorted(seen)
    rng.shuffle(codes)
    return codes
