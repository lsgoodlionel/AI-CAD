"""`DatabaseAdapter` 的能力边界。

**这里有一次误诊值得记住**：看到成片的
`TextClause.bindparams() argument after ** must be a mapping` 后，
我判定「项目统一用 databases（`:name` + 字典），仓里的 `$1` 是遗留缺陷」。

实际上项目有 `DatabaseAdapter`（`dependencies.py`），把 `$1` + 位置参数
归一成 databases 风格；生产路径 `tasks/regulation_import.py` 传的正是
`DatabaseAdapter(raw_db)`。**`$1` 是既有约定**，全仓 180 余处都靠它工作。
真正的根因是**我的一次性导入脚本传了裸 `database`**，绕过了适配器。

判据写错时，错误不会喊出来——它只会给出一个自洽的、指向别处的解释。

代价之一：适配器**没有 `transaction()`**，而 AGE 建图必须钉住连接
（`LOAD 'age'` 是会话级的）。补上，否则生产路径的建图整块失败。
"""
import pytest

from dependencies import DatabaseAdapter


class FakeDB:
    def __init__(self):
        self.calls = []
        self.tx_opened = 0

    async def fetch_one(self, query, values=None):
        self.calls.append((query, values)); return None

    async def execute(self, query, values=None):
        self.calls.append((query, values)); return None

    def transaction(self):
        db = self
        class Tx:
            async def __aenter__(self): db.tx_opened += 1; return self
            async def __aexit__(self, *exc): return False
        return Tx()


@pytest.mark.asyncio
async def test_positional_placeholders_are_normalised():
    """`$1` + 位置参数 —— 全仓 180 余处依赖的既有约定。"""
    db = FakeDB()
    await DatabaseAdapter(db).execute(
        "UPDATE t SET a=1 WHERE id=$1 AND b=$2", "x", "y")
    query, values = db.calls[0]
    assert ":p1" in query and ":p2" in query and "$1" not in query
    assert values == {"p1": "x", "p2": "y"}


@pytest.mark.asyncio
async def test_named_bindings_pass_through():
    """`:name` + 字典也照样透传——两种写法在适配器下都安全，
    所以给两种调用方式共用的服务函数用 `:name` 是稳妥的。"""
    db = FakeDB()
    await DatabaseAdapter(db).execute(
        "UPDATE t SET a=1 WHERE id = CAST(:book_id AS uuid)", {"book_id": "b1"})
    query, values = db.calls[0]
    assert ":book_id" in query
    assert values == {"book_id": "b1"}


@pytest.mark.asyncio
async def test_adapter_exposes_transaction():
    """AGE 建图必须钉住同一条连接（`LOAD 'age'` 是会话级的）。

    适配器不透出 `transaction()` 时，生产路径（Celery 任务传的是适配器）
    会在建图那一整块抛 AttributeError —— 而它被 except 包着，
    表现为「图节点缺 N」而非报错。
    """
    db = FakeDB()
    async with DatabaseAdapter(db).transaction():
        pass
    assert db.tx_opened == 1
