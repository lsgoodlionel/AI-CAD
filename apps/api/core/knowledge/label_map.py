"""图例名称 → 既有分类法（9 类 taxonomy + 机电系统 + 材料）。

**为什么必须映射**：抽出来的符号标注若只有中文名，下游用不上。
既有识别栈的顶层类别**固定 9 类**（`layer_conventions._KIND_ORDER`），
Phase C 数据集规范写明「不得另起炉灶」，所以这里只做映射，不新增顶层类。

**不硬凑**：图例里大量条目**本来就不是构件** ——
「混凝土」「普通砖」是材料剖面图案，「检查孔」是洞口，
「中性线」「屏蔽导体」是电气图形符号。给它们编一个构件类别
会污染训练标签。判不出就返回 `taxonomy=None` 并给出 `domain`，
让使用方自己决定收不收。

`domain` 的取值与它们各自的用处：
- `material`  建筑材料图例（剖面填充图案）→ 可用于**剖面材质识别**，
              不是构件本身；
- `component` 构造/配件图例 → 能落到 9 类里；
- `opening`   洞口类（孔洞、检查孔）→ 与 door/window 同族但不等同；
- `electrical`/`hvac`/`plumbing`/`fire` 机电图形符号 → equipment 或 pipe；
- `annotation` 标注符号（标高、索引、折断线）→ 不是实体；
- `unmapped`  认不出 —— **如实留白**。

**电气符号必须再分两层**，否则会给建模引入根本不存在的构件：
- **平面图上的设备**（配电箱、灯具、插座、探测器）有空间位置，
  能落到 `equipment`、能建模；
- **系统图/原理图上的电路元件**（晶闸管、PNP 半导体管、延时触点、
  感应电动机符号）**没有空间实体** —— 它们画在配电系统图上表示接线关系。
  这类给 `subclass="circuit_symbol"`、`taxonomy=None`：
  是电气符号没错，但不是三维模型里的任何东西。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: 顶层类别，与 `core.model3d.layer_conventions._KIND_ORDER` 完全一致。
#: **不得增删**（改了会让已标注数据与已训练权重全部错位）。
TAXONOMY_KINDS = ("column", "beam", "slab", "wall", "door", "window",
                  "pipe", "equipment", "axis")

#: 机电系统维度，与 `layer_conventions.classify_system` 一致。
MEP_SYSTEMS = ("fire", "plumbing", "electrical", "hvac")


@dataclass(frozen=True)
class LabelMapping:
    domain: str
    taxonomy: str | None            # 9 类之一；映射不到为 None
    subclass: str | None            # 更细的语义标签（不进 9 类）
    mep_system: str | None
    matched_by: str                 # 命中的关键词，便于人工核对与调参

    def to_dict(self) -> dict:
        return {"domain": self.domain, "taxonomy": self.taxonomy,
                "subclass": self.subclass, "mep_system": self.mep_system,
                "label_matched_by": self.matched_by}


#: 规则表。**顺序即优先级**：先具体后宽泛、专业名词先于通配字。
#: 每条 = (关键词组, domain, taxonomy, subclass, mep_system)
#: 关键词按「出现即命中」匹配（中文无词边界，正则边界不适用）。
_RULES: tuple[tuple[tuple[str, ...], str, str | None, str | None, str | None], ...] = (
    # ── 标注符号：不是实体，最先排除，免得被后面的宽泛词吃掉 ──
    (("标高", "指北针", "折断线", "对称符号", "索引符号", "详图符号",
      "引出线", "尺寸线", "剖切", "定位轴线", "风玫瑰"),
     "annotation", None, "annotation", None),

    # ── 建筑材料图例（GB/T 50001 附录：常用建筑材料图例）──
    # 材料是**剖面填充图案**，不是构件。但混凝土/钢筋混凝土的图案
    # 恰是判断「这个剖面是不是结构构件」的强线索，故单独留 subclass。
    (("钢筋混凝土",), "material", None, "reinforced_concrete", None),
    (("混凝土",), "material", None, "concrete", None),
    (("自然土壤", "夯实土壤", "土壤"), "material", None, "soil", None),
    (("砂、灰土", "砂灰土", "灰土", "砂"), "material", None, "sand", None),
    (("毛石", "石材", "块石"), "material", None, "stone", None),
    (("普通砖", "耐火砖", "空心砖", "饰面砖", "多孔砖", "砖"),
     "material", None, "brick", None),
    (("多孔材料", "泡沫", "保温"), "material", None, "porous", None),
    (("石膏板", "纤维板", "胶合板", "木材", "木"), "material", None, "board", None),
    (("金属", "钢", "铝"), "material", None, "metal", None),
    (("玻璃",), "material", None, "glass", None),
    (("防水材料", "防水", "卷材"), "material", None, "waterproof", None),
    (("粉刷", "抹灰", "饰面"), "material", None, "plaster", None),
    (("液体", "网状材料", "焦渣", "matter"), "material", None, "other", None),

    # ── 机电符号（**必须排在通配构件词之前**）──
    # 电气符号名里常含通配字：`端子板` 含「板」、`电缆桥架` 含「架」。
    # 实测 `端子板` 曾被「板 → slab」抢先命中，成了「楼板」。
    # ── 机电：管线 ──
    (("喷淋", "消火栓", "水泵接合器", "灭火器", "报警阀"),
     "fire", "equipment", "fire_equipment", "fire"),
    (("给水管", "污水管", "废水管", "雨水管", "水管", "地漏", "存水弯",
      "检查口", "清扫口"), "plumbing", "pipe", "pipe_water", "plumbing"),
    (("风管", "风口", "风机", "空调", "散热器", "新风"),
     "hvac", "pipe", "pipe_air", "hvac"),
    (("桥架", "线槽", "穿管", "导线", "电缆", "母线", "接地", "线路",
      "中性线", "中间线", "屏蔽导体", "绞合导线", "连线"),
     "electrical", "pipe", "cable", "electrical"),
    # ── 机电：设备（电气图形符号的主体）──
    # 平面图上的电气**设备**：有空间位置，可建模。
    (("配电箱", "配电柜", "开关柜", "变压器", "电箱", "插座", "插头",
      "灯具", "灯", "照明", "应急", "探测器", "报警", "扬声器", "电话",
      "天线", "摄像", "传感器", "按钮", "端子", "蓄电池", "风扇",
      "热水器", "电度表", "电能表", "配电"),
     "electrical", "equipment", "electrical_equipment", "electrical"),
    # 系统图/原理图上的**电路元件**：无空间实体，`taxonomy=None`。
    (("晶体", "闸流管", "半导体", "二极管", "晶体管", "触点", "绕组",
      "电枢", "整流", "逆变", "变换器", "热电偶", "电阻", "电容",
      "电感", "互感器", "继电器", "接触器", "断路器", "隔离开关",
      "熔断器", "断电器", "保护器件", "操作器件", "连接片", "相序",
      "正极", "负极", "直流", "交流", "加热元件", "电动机", "发电机",
      "电机", "时钟", "母钟", "子钟", "发电站", "变电", "电站",
      "功率表", "电流表", "电压表", "频率计", "相位", "仪表",
      "接触件", "一般符号", "示例", "均衡器", "变频器", "衰减器",
      "放大器", "滤波器", "调制", "振荡"),
     "electrical", None, "circuit_symbol", "electrical"),
    # 兜底：仍带电气特征的**空间设备**。放在电路元件之后 ——
    # `隔离开关` 先归电路，`单极拉线开关` 才落到这里。
    (("避雷", "接闪", "开关", "调光", "定时", "电锁", "电动阀", "电磁阀",
      "阀", "插销", "风机盘管", "电铃", "蜂鸣"),
     "electrical", "equipment", "electrical_equipment", "electrical"),
    (("水箱", "水池", "水泵", "洗脸盆", "浴盆", "大便器", "小便",
      "污水池", "卫生器具"), "plumbing", "equipment", "sanitary", "plumbing"),

    # ── 洞口 / 门窗 ──
    (("检查孔", "孔洞", "坑槽", "墙预留洞", "楼板预留洞", "留洞"),
     "opening", None, "opening", None),
    (("防火门",), "component", "door", "door_fire", "fire"),
    (("门联窗",), "component", "door", "door_combined", None),
    (("空门洞", "单扇门", "双扇门", "转门", "推拉门", "折叠门", "门"),
     "component", "door", "door", None),
    (("高窗", "推拉窗", "百叶窗", "固定窗", "窗"),
     "component", "window", "window", None),

    # ── 构件（能落进 9 类的）──
    (("柱", "墩"), "component", "column", "column", None),
    (("楼梯", "梯段", "台阶", "坡道", "电梯", "自动扶梯"),
     "component", "slab", "stair", None),          # 板式楼梯归 slab（22G101-2）
    (("楼板", "屋面板", "板"), "component", "slab", "slab", None),
    (("梁", "过梁", "圈梁"), "component", "beam", "beam", None),
    (("隔断", "土墙", "幕墙", "隔墙", "填充墙", "墙"),
     "component", "wall", "wall", None),
    (("栏杆", "扶手", "栏板"), "component", None, "railing", None),
)

#: 名称清洗：去掉序号前缀、括注、空白。
_CLEAN_RE = re.compile(r"[（(][^）)]*[）)]|[\s　]+|^[0-9]+[、.．]?")


def _clean(name: str) -> str:
    return _CLEAN_RE.sub("", str(name or ""))


def map_label(name: str, *, note: str = "") -> LabelMapping:
    """图例名称 → 分类。认不出返回 `domain="unmapped"`，**不猜**。

    `note`（说明栏）只在名称本身认不出时参与匹配 —— 说明里常提到
    别的构件（「包括各种自然土壤」），先用它会带偏。
    """
    text = _clean(name)
    for source, candidate in (("name", text), ("note", _clean(note))):
        if not candidate:
            continue
        for keywords, domain, taxonomy, subclass, system in _RULES:
            for keyword in keywords:
                if keyword in candidate:
                    return LabelMapping(domain, taxonomy, subclass, system,
                                        f"{source}:{keyword}")
    return LabelMapping("unmapped", None, None, None, "")


def summarize(mappings: list[LabelMapping]) -> dict:
    """统计各 domain / taxonomy 的条数，供数据卡如实登记覆盖率。"""
    domains: dict[str, int] = {}
    taxonomies: dict[str, int] = {}
    for m in mappings:
        domains[m.domain] = domains.get(m.domain, 0) + 1
        key = m.taxonomy or "(none)"
        taxonomies[key] = taxonomies.get(key, 0) + 1
    return {"by_domain": dict(sorted(domains.items(), key=lambda kv: -kv[1])),
            "by_taxonomy": dict(sorted(taxonomies.items(), key=lambda kv: -kv[1])),
            "total": len(mappings)}
