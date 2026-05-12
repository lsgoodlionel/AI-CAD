"""
规则引擎：基于 YAML DSL 的静态规则评估。
- 从 data/rules/{discipline}.yaml + common.yaml 加载规则
- 也从 regulation_articles 表获取 DB 规则（tags 匹配）
- 纯 Python，无 ML 依赖，速度最快
"""
import re
import logging
from pathlib import Path

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

from .base import BaseEngine, DrawingContext, AIIssue, IssueSeverity

logger = logging.getLogger(__name__)

_RULES_DIR = Path(__file__).parents[2] / "data" / "rules"

_SEVERITY_MAP = {
    "critical": IssueSeverity.CRITICAL,
    "major":    IssueSeverity.MAJOR,
    "minor":    IssueSeverity.MINOR,
    "info":     IssueSeverity.INFO,
}


def _load_yaml_rules(discipline: str) -> list[dict]:
    if not _HAS_YAML:
        logger.warning("pyyaml 未安装，跳过 YAML 规则加载")
        return []

    rules: list[dict] = []
    for fname in ("common.yaml", f"{discipline}.yaml"):
        path = _RULES_DIR / fname
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                rules.extend(data.get("rules", []))
            except Exception as e:
                logger.error("加载规则文件 %s 失败: %s", path, e)
    return rules


def _safe_fmt(template: str, ctx: dict) -> str:
    try:
        return template.format(**{k: (v or "") for k, v in ctx.items()})
    except (KeyError, ValueError):
        return template


def _eval_condition(cond: dict, ctx: dict) -> bool:
    """Returns True → issue should be reported."""
    ctype = cond.get("type", "")
    field = cond.get("field", "")
    val = ctx.get(field)

    match ctype:
        case "empty":
            return not val

        case "not_empty":
            return bool(val)

        case "regex":
            text = str(val or "")
            matched = bool(re.search(cond["pattern"], text, re.IGNORECASE | re.MULTILINE))
            # negate=True (default): fire when pattern NOT found (expected pattern missing)
            # negate=False: fire when pattern IS found (forbidden pattern present)
            return not matched if cond.get("negate", True) else matched

        case "in":
            return str(val) in [str(v) for v in cond.get("values", [])]

        case "not_in":
            return str(val) not in [str(v) for v in cond.get("values", [])]

        case "contains":
            text = str(val or "")
            substr = str(cond.get("value", ""))
            ci = not cond.get("case_sensitive", False)
            found = (substr.lower() in text.lower()) if ci else (substr in text)
            return not found if cond.get("negate", False) else found

        case "gte":
            return float(val or 0) >= float(cond["value"])
        case "gt":
            return float(val or 0) > float(cond["value"])
        case "lte":
            return float(val or 0) <= float(cond["value"])
        case "lt":
            return float(val or 0) < float(cond["value"])
        case "eq":
            return str(val) == str(cond["value"])
        case "neq":
            return str(val) != str(cond["value"])

        case "and":
            return all(_eval_condition(c, ctx) for c in cond.get("conditions", []))
        case "or":
            return any(_eval_condition(c, ctx) for c in cond.get("conditions", []))

        case _:
            return False


class RulesEngine(BaseEngine):
    engine_name = "rules"

    async def analyze(self, ctx: DrawingContext, db) -> list[AIIssue]:
        issues: list[AIIssue] = []
        ctx_dict = ctx.as_dict()

        # ── 1. YAML 静态规则 ──────────────────────────────────
        yaml_rules = _load_yaml_rules(ctx.discipline)
        for rule in yaml_rules:
            try:
                cond = rule.get("condition", {})
                if not _eval_condition(cond, ctx_dict):
                    continue
                issues.append(AIIssue(
                    engine=self.engine_name,
                    severity=_SEVERITY_MAP.get(rule.get("severity", "info"), IssueSeverity.INFO),
                    description=_safe_fmt(rule.get("message", rule["name"]), ctx_dict),
                    category=rule.get("category", ""),
                    regulation_ref=rule.get("regulation_ref", ""),
                    suggestion=_safe_fmt(rule.get("suggestion", ""), ctx_dict),
                ))
            except Exception as e:
                logger.warning("规则 %s 评估失败: %s", rule.get("id", "?"), e)

        # ── 2. DB 规则（regulation_articles 中有 rule_condition 字段的条目）──
        try:
            db_rules = await db.fetch_all(
                """
                SELECT article_no, title, content, tags
                FROM regulation_articles
                WHERE rule_condition IS NOT NULL
                  AND ($1 = ANY(tags) OR 'common' = ANY(tags))
                LIMIT 50
                """,
                ctx.discipline,
            )
            for row in db_rules:
                issues.append(AIIssue(
                    engine=self.engine_name,
                    severity=IssueSeverity.INFO,
                    description=f"[规范参考] {row['title']}",
                    category="规范引用",
                    regulation_ref=row["article_no"],
                ))
        except Exception as e:
            logger.debug("DB 规则查询失败（可忽略）: %s", e)

        logger.info("[RulesEngine] 图纸 %s 共检出 %d 条问题", ctx.drawing_no, len(issues))
        return issues
