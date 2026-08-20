"""系统提示的传递路径。

**实测暴露**：`regulation_importer` 是全仓唯一用 `system=` 调 `route()` 的
调用方，而 `ModelRouter.route()` 根本没有这个参数——每次都抛
`TypeError: got an unexpected keyword argument 'system'`，被 except 吞成
warning 后落回本地正则降级。规范 NLP 的分类与深度抽取**从未真正跑过**
（75/75 全失败），而导入照常报成功。

修法放在 provider 层：系统提示统一写成 `{"role": "system"}` 消息，
由各 provider 按自家 API 的要求转换——Anthropic 的 Messages API
**拒绝 system 角色的消息**，必须走独立的 `system` 字段；
OpenAI 兼容 / Ollama 则原生接受。
"""
import pytest

from core.llm.providers.base import ModelParams


@pytest.mark.unit
def test_leading_system_messages_are_split_out():
    from core.llm.providers.base import split_system_messages

    system, rest = split_system_messages([
        {"role": "system", "content": "你是规范提取助手"},
        {"role": "user", "content": "提取这段"},
    ])
    assert system == "你是规范提取助手"
    assert rest == [{"role": "user", "content": "提取这段"}]


@pytest.mark.unit
def test_multiple_system_messages_are_joined():
    from core.llm.providers.base import split_system_messages

    system, rest = split_system_messages([
        {"role": "system", "content": "A"},
        {"role": "system", "content": "B"},
        {"role": "user", "content": "u"},
    ])
    assert system == "A\n\nB"
    assert len(rest) == 1


@pytest.mark.unit
def test_system_message_after_user_is_still_extracted():
    """有的调用方会把系统提示放在后面——不能因为位置就漏掉，
    否则 Anthropic 会直接 400 拒绝整个请求。"""
    from core.llm.providers.base import split_system_messages

    system, rest = split_system_messages([
        {"role": "user", "content": "u"},
        {"role": "system", "content": "S"},
    ])
    assert system == "S"
    assert all(m["role"] != "system" for m in rest)


@pytest.mark.unit
def test_no_system_message_returns_none():
    from core.llm.providers.base import split_system_messages

    system, rest = split_system_messages([{"role": "user", "content": "u"}])
    assert system is None
    assert len(rest) == 1


@pytest.mark.asyncio
async def test_anthropic_provider_sends_system_separately():
    """Anthropic 的 Messages API 不接受 `role: system` 的消息。"""
    from core.llm.providers.anthropic_provider import AnthropicProvider

    captured = {}

    class FakeMessages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            class R:
                content = [type("T", (), {"text": "ok"})()]
                usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()
            return R()

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.client = type("C", (), {"messages": FakeMessages()})()

    await provider.complete(
        [{"role": "system", "content": "S"}, {"role": "user", "content": "u"}],
        ModelParams(model_id="claude-x"))

    assert captured["system"] == "S"
    assert all(m["role"] != "system" for m in captured["messages"])


@pytest.mark.unit
def test_importer_no_longer_passes_system_kwarg():
    """`route()` 没有 `system` 参数——传了就是 100% TypeError。"""
    import inspect

    from core.llm.router import ModelRouter
    import services.regulation_importer as importer

    assert "system" not in inspect.signature(ModelRouter.route).parameters
    source = inspect.getsource(importer)
    assert "system=_EXTRACT_SYSTEM" not in source
    assert "system=_CLASSIFY_SYSTEM" not in source
