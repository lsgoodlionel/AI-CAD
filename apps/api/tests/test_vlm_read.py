"""远程 VLM 读图适配器（qwen3.5-vision）离线测试。

覆盖：文本解析（判专业/标高候选/构件候选，结构化行优先 + 全文兜底）、
置信度过滤、端点解析优先级（DB > env > 均缺时 None，端点全走 mock/env，
不联网、不含任何真实地址）、图像预处理（缩放不放大）、HTTP 调用（mock
httpx.AsyncClient）、整链路降级（端点未配置/图像非法/调用异常均优雅降级
为 backend="none"，不抛错）。
"""
from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from core.model3d.vlm_read import ollama_vlm
from core.model3d.vlm_read.parse import (
    parse_components,
    parse_discipline,
    parse_elevations,
    parse_vlm_text,
)
from core.model3d.vlm_read.types import (
    ComponentCandidate,
    DisciplineCandidate,
    ElevationCandidate,
    VlmReadResult,
)


def _png_bytes(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), color=(200, 200, 200))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# ── parse: 专业判定 ──────────────────────────────────────────────

class TestParseDiscipline:
    def test_structured_line_hits_known_discipline(self):
        text = "专业：结构\n标高：-3.200、-4.700、+15.00\n构件：梁、板、柱、基础底板"
        got = parse_discipline(text)
        assert got == DisciplineCandidate(value="结构", confidence=0.85, evidence="专业：结构")

    def test_structured_line_explicit_unknown_returns_none(self):
        text = "专业：unknown\n标高：无\n构件：无"
        assert parse_discipline(text) is None

    def test_fallback_keyword_scan_when_no_structured_line(self):
        text = "这是一张暖通空调系统图，展示了风管走向。"
        got = parse_discipline(text)
        assert got is not None
        assert got.value == "暖通"
        assert got.confidence == 0.55

    def test_no_discipline_keyword_returns_none(self):
        assert parse_discipline("这张图看不太清楚，无法确定任何信息。") is None


# ── parse: 标高候选 ──────────────────────────────────────────────

class TestParseElevations:
    def test_structured_line_extracts_multiple_values_sorted(self):
        text = "标高：-3.200、-4.700、+15.00"
        got = parse_elevations(text)
        assert [c.value_m for c in got] == [-4.7, -3.2, 15.0]
        assert all(c.confidence == 0.85 for c in got)

    def test_zero_elevation_with_plus_minus_prefix(self):
        text = "标高：±0.000"
        got = parse_elevations(text)
        assert got == (ElevationCandidate(value_m=0.0, confidence=0.85, evidence="±0.000"),)

    def test_dedupes_repeated_values(self):
        text = "标高：+3.600、+3.600"
        got = parse_elevations(text)
        assert len(got) == 1

    def test_out_of_range_value_is_dropped(self):
        # 999.999 超出合理标高范围（模型幻觉/误读），不得进入候选
        text = "标高：999.999、+3.600"
        got = parse_elevations(text)
        assert [c.value_m for c in got] == [3.6]

    def test_fallback_scans_full_text_with_lower_confidence(self):
        text = "这张剖面图显示了 -3.200 处的底板标高。"
        got = parse_elevations(text)
        assert len(got) == 1
        assert got[0].value_m == -3.2
        assert got[0].confidence == 0.55

    def test_no_elevation_returns_empty(self):
        assert parse_elevations("标高：无") == ()

    def test_extracts_from_thinking_when_content_line_has_no_values(self):
        """真实 qwen3.5：content 是精简结论（「标高：见图」无数值）、thinking 是
        详细推理并散落真实标高。合并文本喂进来时必须从自由文本稳健抽到标高，
        而不是因结构行没命中就整段漏空。
        """
        content = (
            "专业：结构\n"
            "标高：见图\n"
            "构件：梁、板、柱、基础底板"
        )
        thinking = (
            "让我仔细看这张结构剖面图。图纸最下方标注了基础底板标高 -4.700，"
            "往上是地下室底板标高 -3.200。首层室内地坪为 ±0.000，"
            "屋面结构标高标注为 +15.00。整体是一栋带一层地下室的结构。"
        )
        raw_text = f"{content}\n{thinking}"
        got = parse_elevations(raw_text)
        assert [c.value_m for c in got] == [-4.7, -3.2, 0.0, 15.0]
        # 全部来自自由文本抽取（结构行无数值）→ 中置信
        assert all(c.confidence == 0.55 for c in got)

    def test_zero_elevation_in_free_text_via_plus_minus(self):
        # 散文里的 ±0.000 无关键词也应命中——± 前缀本身即标高标记
        got = parse_elevations("经推断该处对应 ±0.000 的位置。")
        assert [c.value_m for c in got] == [0.0]
        assert got[0].confidence == 0.55

    def test_structured_values_keep_high_confidence_when_also_in_prose(self):
        """结构行给出的数值即便在 thinking 里再次出现，也保留结构行的高置信。"""
        raw_text = (
            "专业：结构\n标高：-3.200、+15.00\n构件：梁、柱\n"
            "推理：底板标高 -3.200，另有女儿墙顶 +15.00，还发现 -4.700 的基础底板。"
        )
        got = parse_elevations(raw_text)
        by_value = {c.value_m: c.confidence for c in got}
        assert by_value[-3.2] == 0.85  # 结构行命中，高置信
        assert by_value[15.0] == 0.85
        assert by_value[-4.7] == 0.55  # 仅 thinking 命中，中置信
        assert [c.value_m for c in got] == [-4.7, -3.2, 15.0]

    def test_decimal_without_elevation_context_is_not_extracted(self):
        # 缩放系数 0.27 / 比例等裸小数无标高语境，绝不误抽为标高
        assert parse_elevations("图像缩放矩阵约 0.27，重编码为 PNG 后上传。") == ()

    def test_version_like_decimal_not_extracted(self):
        # 版本号「3.50」这类无语境小数不得进入标高候选
        assert parse_elevations("模型 qwen3.50 完成本次读图。") == ()

    def test_out_of_range_value_in_free_text_dropped(self):
        # 带标高语境但数值超出 [-30, 300]（模型幻觉）→ 仍被值域过滤
        got = parse_elevations("推理得到底板标高 -4.700 与荒谬的标高 888.000。")
        assert [c.value_m for c in got] == [-4.7]


# ── parse: 构件候选 ──────────────────────────────────────────────

class TestParseComponents:
    def test_structured_line_extracts_distinct_labels(self):
        text = "构件：梁、板、柱、基础底板"
        got = parse_components(text)
        labels = [c.label for c in got]
        assert set(labels) == {"梁", "板", "柱", "基础底板"}
        assert all(c.confidence == 0.85 for c in got)

    def test_longer_label_suppresses_shorter_substring_duplicate(self):
        # "基础底板"命中时不应再额外产出裸露的"基础"候选
        text = "构件：基础底板、承台"
        got = parse_components(text)
        labels = [c.label for c in got]
        assert "基础底板" in labels
        assert "基础" not in labels

    def test_no_component_returns_empty(self):
        assert parse_components("构件：无") == ()

    def test_fallback_scan_lower_confidence(self):
        text = "图中可见若干道梁和柱子的布置。"
        got = parse_components(text)
        assert any(c.label == "梁" and c.confidence == 0.55 for c in got)


# ── parse_vlm_text: 整合 ────────────────────────────────────────

class TestParseVlmText:
    def test_typical_structured_response(self):
        text = (
            "专业：结构\n"
            "标高：-3.200、-4.700、+15.00\n"
            "构件：梁、板、柱、基础底板"
        )
        result = parse_vlm_text(text, model="qwen3.5:latest")
        assert result.available
        assert result.backend == "qwen3.5-vision"
        assert result.discipline.value == "结构"
        assert [e.value_m for e in result.elevations] == [-4.7, -3.2, 15.0]
        assert {c.label for c in result.components} == {"梁", "板", "柱", "基础底板"}

    def test_empty_text_degrades_to_unavailable(self):
        result = parse_vlm_text("")
        assert not result.available
        assert result.backend == "none"
        assert result.warnings

    def test_whitespace_only_text_degrades_to_unavailable(self):
        result = parse_vlm_text("   \n  ")
        assert not result.available


# ── VlmReadResult.filter_confidence ─────────────────────────────

class TestFilterConfidence:
    def test_drops_low_confidence_candidates(self):
        result = VlmReadResult(
            discipline=DisciplineCandidate(value="结构", confidence=0.55),
            elevations=(
                ElevationCandidate(value_m=-3.2, confidence=0.85),
                ElevationCandidate(value_m=15.0, confidence=0.55),
            ),
            components=(ComponentCandidate(label="梁", confidence=0.85),),
            backend="qwen3.5-vision",
        )
        filtered = result.filter_confidence(0.6)
        assert filtered.discipline is None
        assert [e.value_m for e in filtered.elevations] == [-3.2]
        assert [c.label for c in filtered.components] == ["梁"]

    def test_to_dict_roundtrips_none_discipline(self):
        result = VlmReadResult(backend="none")
        d = result.to_dict()
        assert d["available"] is False
        assert d["discipline"] is None


# ── 图像预处理 ───────────────────────────────────────────────────

class TestPrepareImage:
    def test_scales_down_oversized_image(self):
        raw = _png_bytes(4718, 3338)
        scaled = ollama_vlm.prepare_image(raw, max_dim_px=1280)
        image = Image.open(io.BytesIO(scaled))
        assert max(image.size) <= 1280
        # 等比缩放：宽高比不变（容差 1px 舍入误差）
        assert abs(image.width / image.height - 4718 / 3338) < 0.01

    def test_does_not_upscale_small_image(self):
        raw = _png_bytes(200, 150)
        scaled = ollama_vlm.prepare_image(raw, max_dim_px=1280)
        image = Image.open(io.BytesIO(scaled))
        assert image.size == (200, 150)

    def test_invalid_bytes_raise(self):
        with pytest.raises(Exception):
            ollama_vlm.prepare_image(b"not an image")


# ── 端点解析：DB > env > None，端点绝不硬编码 ───────────────────

class TestResolveBaseUrl:
    @pytest.mark.asyncio
    async def test_prefers_db_over_env(self, monkeypatch):
        monkeypatch.setenv(ollama_vlm._ENV_BASE_URL, "http://env-should-be-ignored:11434")
        with patch.object(ollama_vlm, "_resolve_base_url_from_db", AsyncMock(return_value="http://from-db:11434")):
            got = await ollama_vlm.resolve_base_url()
        assert got == "http://from-db:11434"

    @pytest.mark.asyncio
    async def test_falls_back_to_env_when_db_empty(self, monkeypatch):
        monkeypatch.setenv(ollama_vlm._ENV_BASE_URL, "http://localhost:11434")
        with patch.object(ollama_vlm, "_resolve_base_url_from_db", AsyncMock(return_value=None)):
            got = await ollama_vlm.resolve_base_url()
        assert got == "http://localhost:11434"

    @pytest.mark.asyncio
    async def test_returns_none_when_neither_configured(self, monkeypatch):
        monkeypatch.delenv(ollama_vlm._ENV_BASE_URL, raising=False)
        with patch.object(ollama_vlm, "_resolve_base_url_from_db", AsyncMock(return_value=None)):
            got = await ollama_vlm.resolve_base_url()
        assert got is None

    @pytest.mark.asyncio
    async def test_db_lookup_failure_degrades_silently(self):
        """DB 未连接是常态（CLI/单测场景），不应抛错——真实 databases.Database
        未 connect() 时 fetch_one 会抛异常，验证该路径被吞掉、不传播。
        """
        got = await ollama_vlm._resolve_base_url_from_db()
        assert got is None


# ── call_vlm_chat：mock httpx.AsyncClient（不联网） ──────────────

class TestCallVlmChat:
    @pytest.mark.asyncio
    async def test_posts_to_api_chat_and_returns_content(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"message": {"content": "专业：结构\n标高：无\n构件：无"}}
        mock_response.raise_for_status = MagicMock()

        captured: dict = {}

        class MockClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, **kwargs):
                captured["url"] = url
                captured["json"] = json
                return mock_response

        with patch("core.model3d.vlm_read.ollama_vlm.httpx.AsyncClient", return_value=MockClient()):
            content = await ollama_vlm.call_vlm_chat(
                b"fake-png-bytes", "prompt", base_url="http://placeholder:11434", model="qwen3.5:latest"
            )

        assert content == "专业：结构\n标高：无\n构件：无"
        assert captured["url"] == "http://placeholder:11434/api/chat"
        assert captured["json"]["model"] == "qwen3.5:latest"
        assert captured["json"]["stream"] is False
        assert captured["json"]["messages"][0]["images"] == [
            __import__("base64").b64encode(b"fake-png-bytes").decode("ascii")
        ]

    @pytest.mark.asyncio
    async def test_merges_thinking_field_into_returned_text(self):
        """思考模型（qwen3.5）把详细推理放独立 ``thinking`` 字段——须并入返回
        文本，否则散在 thinking 的标高全丢。content 有值时二者拼接。
        """
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {
                "content": "专业：结构\n标高：见图\n构件：梁、柱",
                "thinking": "基础底板标高 -4.700，首层 ±0.000。",
            }
        }
        mock_response.raise_for_status = MagicMock()

        class MockClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, **kwargs):
                return mock_response

        with patch("core.model3d.vlm_read.ollama_vlm.httpx.AsyncClient", return_value=MockClient()):
            content = await ollama_vlm.call_vlm_chat(
                b"x", "p", base_url="http://placeholder:11434"
            )

        assert "标高：见图" in content
        assert "-4.700" in content
        # 拼接后交解析器应能从 thinking 抽到标高
        got = parse_elevations(content)
        assert [c.value_m for c in got] == [-4.7, 0.0]

    @pytest.mark.asyncio
    async def test_non_string_content_raises(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"message": {"content": {"unexpected": "structure"}}}
        mock_response.raise_for_status = MagicMock()

        class MockClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, **kwargs):
                return mock_response

        with patch("core.model3d.vlm_read.ollama_vlm.httpx.AsyncClient", return_value=MockClient()):
            with pytest.raises(ValueError):
                await ollama_vlm.call_vlm_chat(b"x", "p", base_url="http://placeholder:11434")


# ── read_drawing_vlm：整链路降级（mock 各环节，不联网） ──────────

class TestReadDrawingVlm:
    @pytest.mark.asyncio
    async def test_degrades_when_endpoint_unconfigured(self):
        with patch.object(ollama_vlm, "resolve_base_url", AsyncMock(return_value=None)):
            result = await ollama_vlm.read_drawing_vlm(b"irrelevant")
        assert not result.available
        assert result.backend == "none"
        assert result.warnings

    @pytest.mark.asyncio
    async def test_degrades_on_invalid_image(self):
        result = await ollama_vlm.read_drawing_vlm(b"not-an-image", base_url="http://placeholder:11434")
        assert not result.available
        assert "图像预处理失败" in result.warnings[0]

    @pytest.mark.asyncio
    async def test_degrades_on_call_failure(self):
        raw = _png_bytes(100, 100)
        with patch.object(ollama_vlm, "call_vlm_chat", AsyncMock(side_effect=RuntimeError("timeout"))):
            result = await ollama_vlm.read_drawing_vlm(raw, base_url="http://placeholder:11434")
        assert not result.available
        assert "远程 VLM 调用失败" in result.warnings[0]

    @pytest.mark.asyncio
    async def test_success_path_returns_parsed_candidates(self):
        raw = _png_bytes(4718, 3338)
        canned = "专业：结构\n标高：-3.200、-4.700、+15.00\n构件：梁、板、柱、基础底板"
        with patch.object(ollama_vlm, "call_vlm_chat", AsyncMock(return_value=canned)) as mocked_call:
            result = await ollama_vlm.read_drawing_vlm(
                raw, base_url="http://placeholder:11434", model="qwen3.5:latest"
            )

        assert result.available
        assert result.backend == "qwen3.5-vision"
        assert result.discipline.value == "结构"
        assert [e.value_m for e in result.elevations] == [-4.7, -3.2, 15.0]
        assert {c.label for c in result.components} == {"梁", "板", "柱", "基础底板"}
        # 喂给远程调用的图像必须已缩放，不是原始 4718×3338 大图
        sent_image_bytes = mocked_call.call_args.args[0]
        sent_image = Image.open(io.BytesIO(sent_image_bytes))
        assert max(sent_image.size) <= 1280
