"""Offline risk checks and opt-in writing advice; not a document style gate."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SUPPORTED_LOG_TYPES = ("release", "engineering", "project", "incident", "decision", "experiment", "audit", "support")
SUPPORTED_PROFILES = ("full", "append")
REQUIRED_FIELDS = {
    "release": (
        ("actual_delivery", "actual delivery", "actual_delivery_date", "actual delivery date", "实际交付", "实际交付日期"),
        ("module", "模块"),
        ("user_impact", "user impact", "用户影响"),
        ("scope", "范围"),
        ("limitations", "限制"),
    ),
    "engineering": (
        ("change_scope", "change scope", "变更范围"),
        ("dependencies_or_migration", "dependencies or migration", "依赖或迁移"),
        ("verification", "验证"),
        ("risk", "风险"),
        ("rollback_condition", "rollback condition", "回滚条件"),
    ),
    "incident": (
        ("status", "状态"),
        ("severity", "严重程度", "严重级别"),
        ("timezone", "time zone", "时区"),
        ("impact", "影响"),
        ("timeline", "时间线"),
        ("recovery", "恢复"),
        ("root_cause_confidence", "root cause confidence", "根因置信度"),
    ),
    "decision": (
        ("context", "背景"),
        ("options", "选项", "候选方案"),
        ("decision", "决策", "决定"),
        ("rationale", "理由"),
        ("tradeoffs", "trade-offs", "权衡", "代价"),
        ("review_condition", "review condition", "复审条件"),
    ),
    "project": (
        ("period", "周期", "期间"),
        ("overall_status", "overall status", "整体状态", "总体状态"),
        ("progress", "进展"),
        ("risks", "风险"),
        ("decisions", "决策", "决定"),
        ("next_milestone", "next milestone", "下一里程碑"),
    ),
    "audit": (
        ("scope", "范围"),
        ("period", "周期", "期间"),
        ("evidence", "证据"),
        ("findings", "发现"),
        ("risk", "风险"),
        ("remediation_status", "remediation status", "整改状态"),
    ),
    "experiment": (
        ("hypothesis", "假设"),
        ("sample", "样本"),
        ("metric_definition", "metric definition", "指标定义"),
        ("results", "结果"),
        ("uncertainty", "不确定性"),
        ("conclusion", "结论"),
    ),
    "support": (
        ("issue", "问题"),
        ("customer_impact", "customer impact", "客户影响"),
        ("handling_or_result", "handling or result", "处理或结果"),
        ("evidence", "证据"),
        ("status", "状态"),
        ("follow_up", "follow-up", "follow up", "后续"),
    ),
}
DATE_FIELDS = {
    "release": ("actual_delivery_date", "published_at", "effective_at", "实际交付日期", "发布时间", "生效时间"),
    "engineering": ("occurred_at", "recorded_at", "发生时间", "记录时间"),
    "incident": ("occurred_at", "发生时间"),
    "decision": ("effective_at", "recorded_at", "生效时间", "记录时间"),
    "project": ("updated_at", "recorded_at", "更新时间", "记录时间"),
    "audit": ("recorded_at", "记录时间"),
    "experiment": ("occurred_at", "recorded_at", "发生时间", "记录时间"),
    "support": ("occurred_at", "updated_at", "发生时间", "更新时间"),
}
ALL_DATE_FIELDS = {alias for aliases in DATE_FIELDS.values() for alias in aliases}

_OBVIOUS_SECRET = re.compile(
    r"\b(?:sk|pk)-[A-Za-z0-9_-]{16,}\b"
    r"|\b(?:api[_ -]?key|token|secret)\b\s*[:=]\s*[^\s]{8,}"
    r"|\b(?:password|cookie|session)\b\s*[:=]\s*[^\s]+"
    r"|\bbearer\s+[A-Za-z0-9._~+/=-]{8,}",
    re.I,
)
_POSSIBLE_PII = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|(?<![\w-])\+\d{1,3}(?:[\s.-]*\(?\d{2,4}\)?){2,4}(?![\w-])"
    r"|(?<![\w-])(?:\+?\d{3}[- ]?\d{3,4}[- ]?\d{4})(?![\w-])"
)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: str = "warning"


_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_INLINE_FIELD = re.compile(
    r"^\s*([A-Za-z_ -]+|[\u4e00-\u9fff]+)\s*[:：]\s*(.*?)\s*$"
)


def _fields(draft: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    lines = draft.splitlines()
    for index, line in enumerate(lines):
        heading = _HEADING.match(line)
        if heading:
            label = heading.group(1).strip().lower()
            value = ""
            for following in lines[index + 1 :]:
                stripped = following.strip()
                if not stripped:
                    continue
                if _HEADING.match(following) or _INLINE_FIELD.match(following):
                    break
                value = stripped
                break
            fields.setdefault(label, []).append(value)
            continue
        inline = _INLINE_FIELD.match(line)
        if inline:
            fields.setdefault(inline.group(1).strip().lower(), []).append(
                inline.group(2).strip()
            )
    return fields


def _matching_values(
    fields: dict[str, list[str]], aliases: tuple[str, ...] | set[str]
) -> list[str]:
    accepted = {alias.lower() for alias in aliases}
    return [
        value
        for label, values in fields.items()
        if label in accepted
        for value in values
    ]


def _valid_date_value(value: str) -> bool:
    if value.casefold() == "unknown" or value == "待确认":
        return True
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _valid_timezone_value(value: str) -> bool:
    if not value:
        return False
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def lint_draft(
    draft: str, *, log_type: str, profile: str = "full",
    check_template: bool = False, style_advice: bool = False,
) -> list[Issue]:
    """Return errors and review hints without inferring the document's style.

    full/append describe the scope of the supplied text. Completeness of prose,
    factual support, and applicability of warnings require contextual review.
    """
    if log_type not in SUPPORTED_LOG_TYPES:
        raise ValueError("unsupported log type: " + log_type)
    if profile not in SUPPORTED_PROFILES:
        raise ValueError("unsupported lint profile: " + profile)
    if check_template and profile != "full":
        raise ValueError("template checking applies only to a full record")
    issues: list[Issue] = []
    def add(code: str, message: str, severity: str = "warning") -> None:
        if code not in {issue.code for issue in issues}:
            issues.append(Issue(code, message, severity))

    fields = _fields(draft)
    labels = set(fields)
    if not draft.strip():
        add("empty_draft", "A log draft must contain text.", "error")
    if check_template:
        groups = REQUIRED_FIELDS[log_type]
        missing = [group for group in groups if not labels.intersection(alias.lower() for alias in group)]
        if missing:
            add("missing_required_field", "Suggested template labels are absent; review the content, including prose and parent context, before adding fields.")
        if not _matching_values(fields, DATE_FIELDS[log_type]):
            add("missing_date", "No template date label was found; check whether the applicable time is already clear in context.")
    date_values = _matching_values(fields, ALL_DATE_FIELDS)
    if any(not _valid_date_value(value) for value in date_values):
        add("invalid_date", "A date field is not recognized as ISO 8601; review its meaning and the document's date convention, without automatically reformatting it.")
    timezone_aliases = {"timezone", "time zone", "时区"}
    timezone_values = _matching_values(fields, timezone_aliases)
    if not timezone_values and check_template:
        add("missing_timezone", "No template timezone label was found; check whether a timezone is relevant and already established.")
    if any(not _valid_timezone_value(value) for value in timezone_values):
        add("invalid_timezone", "A timezone is not recognized as an IANA zone; review it in context rather than imposing a display format.")
    previous = 0
    for match in re.finditer(r"(?m)^(#{1,6})\s+", draft):
        level = len(match.group(1))
        if style_advice and previous and level > previous + 1:
            add("heading_level_jump", "Check whether the heading jump is intentional in the existing document hierarchy.")
        previous = level
    if style_advice and (re.search(r"(?m)^\s*[\U0001f534\U0001f7e1\U0001f7e2]\s*$", draft) or re.search(r"<span\b[^>]*\bcolor\s*:[^>]*>\s*(?:[●■◆]|\U0001f534|\U0001f7e1|\U0001f7e2)\s*</span>\s*$", draft, re.I | re.M)):
        add("color_only_status", "Check that readers can understand the status from the text or an existing legend, including without color.")
    if re.search(r"(?:customer update|客户(?:更新|通知)).{0,200}(?:/admin\b|internal\b|endpoint\b|MobileCategoryPanel\b|Vitest\b|\bSQL\b|内部(?:接口|实现))", draft, re.I | re.S):
        add("internal_implementation_leak", "Review whether these technical terms suit this document's audience; a keyword match does not prove disclosure.")
    if re.search(r"\bstatistically significant\b|统计显著性|具有显著性|显著提升", draft, re.I):
        add("unsupported_significance_claim", "Review the claim and its evidence in context, including negation; this keyword check cannot establish whether it is supported.")
    if _OBVIOUS_SECRET.search(draft):
        add("obvious_secret", "Draft appears to contain a secret; resolve or remove it before writing.", "error")
    if _POSSIBLE_PII.search(draft):
        add("possible_pii", "Review whether this contact information is public, necessary, and authorized; redact unnecessary personal data.")
    markdown_images = list(re.finditer(r"!\[([^\]]*)\]\(([^)]*)\)", draft))
    for image in markdown_images:
        if style_advice and not image.group(1).strip():
            add("missing_visual_caption", "Check whether adjacent text already explains the image or whether it is decorative; follow the document's visual policy.")
    html_images = list(re.finditer(r"<img\b([^>]*)>", draft, re.I))
    html_descriptors: list[str] = []
    for image in html_images:
        alt = re.search(r"\balt\s*=\s*(['\"])(.*?)\1", image.group(1), re.I | re.S)
        if style_advice and (not alt or not alt.group(2).strip()):
            add("missing_visual_caption", "Check whether adjacent text already explains the image or whether it is decorative; follow the document's visual policy.")
        src = re.search(r"\bsrc\s*=\s*(['\"])(.*?)\1", image.group(1), re.I | re.S)
        html_descriptors.append(
            " ".join(
                part
                for part in (
                    alt.group(2) if alt else "",
                    src.group(2) if src else "",
                )
                if part
            )
        )
    for line in draft.splitlines():
        if style_advice and line.count("|") >= 8:
            add("wide_table", "Check readability of this wide table on the intended reading surface; there is no universal column limit.")
    visual_descriptors = [
        image.group(1) + " " + image.group(2) for image in markdown_images
    ] + html_descriptors
    chart_visual = any(
        re.search(r"\b(?:chart|trend|comparison)\b|图表|趋势|对比", descriptor, re.I)
        for descriptor in visual_descriptors
    )
    if style_advice and chart_visual:
        context_groups = (("time range", "时间范围"), ("unit", "单位"), ("source", "来源"), ("summary", "text summary", "文字摘要", "正文摘要"))
        if any(not labels.intersection(alias.lower() for alias in group) for group in context_groups):
            add("incomplete_visual_context", "Review whether applicable context and conclusions are clear in the chart, nearby text, or parent section; labels need not be repeated.")
    if style_advice and re.search(r"\bwhiteboard\b|白板", draft, re.I) and not labels.intersection({"text summary", "正文摘要", "等价正文"}):
        add("missing_whiteboard_text", "Check whether the document already explains the whiteboard's relevant conclusions in text.")
    if log_type in {"incident", "audit", "decision"} and re.search(r"rewrote? the earlier entry.*(?:no longer visible|removed)|重写.*(?:不可见|删除)", draft, re.I | re.S):
        add("silent_history_rewrite", "Review the actual diff and context for a history rewrite; this language match is not proof. Preserve published sensitive history.")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(description="Lint a Feishu cloud-log draft without network access.")
    parser.add_argument("--log-type", required=True, choices=SUPPORTED_LOG_TYPES)
    parser.add_argument("--profile", choices=SUPPORTED_PROFILES, default="full", help="Describe a full record or an append fragment; neither forces template labels or a writing style.")
    parser.add_argument("--check-template", action="store_true", help="Opt in to non-blocking template-label hints for full records, only when a template is requested.")
    parser.add_argument("--style-advice", action="store_true", help="Opt in to non-blocking generic layout hints when the task needs them.")
    parser.add_argument("--file", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        issues = lint_draft(args.file.read_text(encoding="utf-8"), log_type=args.log_type, profile=args.profile, check_template=args.check_template, style_advice=args.style_advice)
    except (OSError, UnicodeError, TypeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    has_errors = any(issue.severity == "error" for issue in issues)
    print(json.dumps({"ok": not has_errors, "issues": [asdict(issue) for issue in issues]}, ensure_ascii=False, sort_keys=True))
    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
