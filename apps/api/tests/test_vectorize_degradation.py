"""向量化的降级：不在导入时下载模型。

**实测**：Chroma 首次调用会现场下载默认嵌入模型（all-MiniLM-L6-v2，79MB），
容器内网速约 30KB/s —— **2 分 20 秒只到 6%**，整批 31 本规范全被这一个
下载卡住。而且 all-MiniLM-L6-v2 **是英文模型**，用它给中文规范条文做向量
本身就不对（引擎参数里配的是 bge-m3）。

按项目既有的设计约束（`docs/MODELING_PIPELINE_BLUEPRINT.md` §7）：
**缺失不得阻断、降级必须可见**。所以模型没就绪时跳过向量化并明确记账，
导入照常完成；向量另行回填。
"""
import pytest


@pytest.mark.unit
def test_missing_model_reports_not_ready(tmp_path):
    from services.regulation_importer import embedding_model_ready

    assert not embedding_model_ready(cache_dir=tmp_path / "nope")


@pytest.mark.unit
def test_partial_download_is_not_ready(tmp_path):
    """下到一半的模型比没有更危险——目录在、文件不全。"""
    from services.regulation_importer import embedding_model_ready

    cache = tmp_path / "onnx_models" / "all-MiniLM-L6-v2"
    cache.mkdir(parents=True)
    (cache / "onnx.tar.gz").write_bytes(b"partial")
    assert not embedding_model_ready(cache_dir=tmp_path / "onnx_models")


@pytest.mark.unit
def test_extracted_model_is_ready(tmp_path):
    from services.regulation_importer import embedding_model_ready

    cache = tmp_path / "onnx_models" / "all-MiniLM-L6-v2" / "onnx"
    cache.mkdir(parents=True)
    (cache / "model.onnx").write_bytes(b"x" * 1024)
    (cache / "tokenizer.json").write_text("{}")
    assert embedding_model_ready(cache_dir=tmp_path / "onnx_models")


@pytest.mark.asyncio
async def test_vectorize_skips_when_model_not_ready(monkeypatch):
    """模型没就绪时**直接跳过**，不触发下载、不抛异常、不阻断导入。"""
    import services.regulation_importer as importer

    monkeypatch.setattr(importer, "embedding_model_ready", lambda **kw: False)

    called = []
    monkeypatch.setattr(importer, "_chroma_collection",
                        lambda: called.append("connected"))

    class DB:
        async def fetch_one(self, *a, **kw):
            called.append("fetched")
            return None

    await importer.vectorize_articles(DB(), ["a1"])
    assert "connected" not in called, "模型没就绪却仍去连 Chroma（会触发下载）"
