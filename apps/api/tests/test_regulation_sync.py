"""
外部规范 API 定时同步任务测试

覆盖目标:
- HTTP 响应解析（items 路径提取）
- auth_type: api_key / basic / none
- 自定义 response_path 取值
- obligation_level 规范化
- 空响应安全处理
- HTTP 错误处理
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tasks.regulation_api_sync import (
    _fetch_remote_articles,
    _normalize_obligation,
)


# ── _normalize_obligation ────────────────────────────────────────

class TestNormalizeObligation:
    @pytest.mark.parametrize("raw,expected", [
        ("MUST",      "MUST"),
        ("must",      "MUST"),
        ("SHOULD",    "SHOULD"),
        ("should",    "SHOULD"),
        ("MAY",       "MAY"),
        ("MUST_NOT",  "MUST_NOT"),
        ("unknown",   "SHOULD"),
        ("",          "SHOULD"),
        (None,        "SHOULD"),
    ])
    def test_normalization(self, raw, expected):
        assert _normalize_obligation(raw) == expected


# ── _fetch_remote_articles ────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_api_key_auth():
    """api_key 鉴权：请求头应携带 Authorization: Bearer <key>"""
    mock_response = MagicMock()
    mock_response.json.return_value = {"items": [{"content": "条文内容"}]}
    mock_response.raise_for_status = MagicMock()

    captured_headers: dict = {}

    class MockClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, headers=None, auth=None, **kwargs):
            captured_headers.update(headers or {})
            return mock_response

    with patch("tasks.regulation_api_sync.httpx.AsyncClient", return_value=MockClient()):
        items = await _fetch_remote_articles(
            endpoint_url="http://test.api/regulations",
            auth_type="api_key",
            auth_config={"api_key": "test-key-123"},
        )

    assert "Authorization" in captured_headers
    assert "test-key-123" in captured_headers["Authorization"]
    assert len(items) == 1


@pytest.mark.asyncio
async def test_fetch_basic_auth():
    """basic 鉴权：应传递 httpx auth 元组"""
    mock_response = MagicMock()
    mock_response.json.return_value = {"items": [{"content": "c1"}, {"content": "c2"}]}
    mock_response.raise_for_status = MagicMock()

    captured_auth = []

    class MockClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, headers=None, auth=None, **kwargs):
            captured_auth.append(auth)
            return mock_response

    with patch("tasks.regulation_api_sync.httpx.AsyncClient", return_value=MockClient()):
        items = await _fetch_remote_articles(
            endpoint_url="http://test.api/regs",
            auth_type="basic",
            auth_config={"username": "user", "password": "pass"},
        )

    assert captured_auth[0] == ("user", "pass")
    assert len(items) == 2


@pytest.mark.asyncio
async def test_fetch_custom_response_path():
    """自定义 response_path: 'data.articles' 应正确取值"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": {
            "articles": [{"content": "深层路径条文"}]
        }
    }
    mock_response.raise_for_status = MagicMock()

    class MockClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, **kwargs):
            return mock_response

    with patch("tasks.regulation_api_sync.httpx.AsyncClient", return_value=MockClient()):
        items = await _fetch_remote_articles(
            endpoint_url="http://test.api/regs",
            auth_type="none",
            auth_config={"response_path": "data.articles"},
        )

    assert len(items) == 1
    assert items[0]["content"] == "深层路径条文"


@pytest.mark.asyncio
async def test_fetch_http_error_propagates():
    """HTTP 错误应向上传播"""
    import httpx

    class MockClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, **kwargs):
            raise httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())

    with patch("tasks.regulation_api_sync.httpx.AsyncClient", return_value=MockClient()):
        with pytest.raises(httpx.HTTPStatusError):
            await _fetch_remote_articles("http://bad.url", "none", {})


@pytest.mark.asyncio
async def test_fetch_empty_items():
    """响应 items 为空时应返回空列表"""
    mock_response = MagicMock()
    mock_response.json.return_value = {"items": []}
    mock_response.raise_for_status = MagicMock()

    class MockClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, **kwargs):
            return mock_response

    with patch("tasks.regulation_api_sync.httpx.AsyncClient", return_value=MockClient()):
        items = await _fetch_remote_articles("http://test.api/regs", "none", {})

    assert items == []
