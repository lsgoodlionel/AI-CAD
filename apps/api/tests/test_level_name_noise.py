"""楼层名分类的噪声门槛。

**实测**：档案里 12475 条 `level_name` 中 **8114 条超过 8 字**、
1676 条含句读。最长的那些全是电气接地做法的说明句子——
因为句中带「B3层」而被楼层正则命中：

    40×4热镀锌扁钢由基础引来，至标高为—16.8m是B3层）设连接板，
    再用BYJ-1×120SC50沿柱内敷设至标高为+12.4m是（3层）设接地端子板…

**后果不是「多存了几条」**：`level_name` 喂给标高配对
（`level_elevation_pairing`），噪声会配出假楼层——
实测轨道交通冒出一个 `F101 101层`（1 张图、0 构件）。

按长度分布定判据（实测）：
2~3 字全是干净层名（`1F`/`B2`/`10F`/`2夹层`），
4~5 字混合（`C区屋面` 对、`3m2层` 错），
**7 字以上基本是句子片段或图名**（`0m夹层平面图`/`3一层防火分区图`）。
"""
import pytest


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "1F", "B2", "10F", "屋面", "地下二层", "2夹层", "B1夹层", "C区屋面",
])
def test_real_level_names_are_kept(text):
    from core.model3d.ocr.classify import classify_text

    assert classify_text(text)[0] == "level_name", text


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    # 都是真实档案里被误判成 level_name 的
    "40×4热镀锌扁钢由基础引来，至标高为—16.8m是B3层）设连接板",
    "1,3~9层设置，火灾时开启着火层",
    "0m夹层平面图",
    "3一层防火分区图",
    "2层12mm厚不",
    "4-7层平面图",
    "0.200至二层底",
])
def test_sentences_and_drawing_names_are_not_level_names(text):
    """句子片段与图名不是楼层名——它们会配出假楼层。"""
    from core.model3d.ocr.classify import classify_text

    assert classify_text(text)[0] != "level_name", text


@pytest.mark.unit
def test_rejected_text_still_gets_a_category():
    """否掉 `level_name` 不等于丢弃——这些文字仍要进档案，
    只是归到别的类里（说明句子是 `note`，图名是 `title`）。
    静默丢弃会让「原文里明明有」变成一个谜。
    """
    from core.model3d.ocr.classify import classify_text

    category, _ = classify_text("1,3~9层设置，火灾时开启着火层")
    assert category and category != "level_name"


@pytest.mark.unit
@pytest.mark.parametrize("text", ["大歌剧厅屋顶层", "6F（设备层）"])
def test_long_but_real_level_names_survive(text):
    """**我的第一版门槛误杀了这两个**：`大歌剧厅屋顶层` 7 字超了上限，
    `6F（设备层）` 因括号被当成句读。既有用例正确地报了失败——
    带部位的层名可以不短，而括号不是句读。"""
    from core.model3d.ocr.classify import classify_text

    assert classify_text(text)[0] == "level_name", text


@pytest.mark.unit
def test_unit_mixed_text_is_rejected_even_when_short():
    """长度挡不住 `2层12mm厚不`（8 字）——「数字+单位混排」
    才是它与真层名的实际分界。"""
    from core.model3d.ocr.classify import classify_text

    assert classify_text("2层12mm厚不")[0] != "level_name"
