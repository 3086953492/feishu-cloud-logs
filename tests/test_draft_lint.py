import importlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


class DraftLintContractTests(unittest.TestCase):
    APPROVED_FIELDS = {
        "release": (
            (("actual_delivery", "actual delivery", "actual_delivery_date", "actual delivery date"), ("\u5b9e\u9645\u4ea4\u4ed8", "\u5b9e\u9645\u4ea4\u4ed8\u65e5\u671f")),
            (("module",), ("\u6a21\u5757",)),
            (("user_impact", "user impact"), ("\u7528\u6237\u5f71\u54cd",)),
            (("scope",), ("\u8303\u56f4",)),
            (("limitations",), ("\u9650\u5236",)),
        ),
        "engineering": (
            (("change_scope", "change scope"), ("\u53d8\u66f4\u8303\u56f4",)),
            (("dependencies_or_migration", "dependencies or migration"), ("\u4f9d\u8d56\u6216\u8fc1\u79fb",)),
            (("verification",), ("\u9a8c\u8bc1",)),
            (("risk",), ("\u98ce\u9669",)),
            (("rollback_condition", "rollback condition"), ("\u56de\u6eda\u6761\u4ef6",)),
        ),
        "incident": (
            (("status",), ("\u72b6\u6001",)),
            (("severity",), ("\u4e25\u91cd\u7a0b\u5ea6", "\u4e25\u91cd\u7ea7\u522b")),
            (("timezone", "time zone"), ("\u65f6\u533a",)),
            (("impact",), ("\u5f71\u54cd",)),
            (("timeline",), ("\u65f6\u95f4\u7ebf",)),
            (("recovery",), ("\u6062\u590d",)),
            (("root_cause_confidence", "root cause confidence"), ("\u6839\u56e0\u7f6e\u4fe1\u5ea6",)),
        ),
        "decision": (
            (("context",), ("\u80cc\u666f",)),
            (("options",), ("\u9009\u9879", "\u5019\u9009\u65b9\u6848")),
            (("decision",), ("\u51b3\u7b56", "\u51b3\u5b9a")),
            (("rationale",), ("\u7406\u7531",)),
            (("tradeoffs", "trade-offs"), ("\u6743\u8861", "\u4ee3\u4ef7")),
            (("review_condition", "review condition"), ("\u590d\u5ba1\u6761\u4ef6",)),
        ),
        "project": (
            (("period",), ("\u5468\u671f", "\u671f\u95f4")),
            (("overall_status", "overall status"), ("\u6574\u4f53\u72b6\u6001", "\u603b\u4f53\u72b6\u6001")),
            (("progress",), ("\u8fdb\u5c55",)),
            (("risks",), ("\u98ce\u9669",)),
            (("decisions",), ("\u51b3\u7b56", "\u51b3\u5b9a")),
            (("next_milestone", "next milestone"), ("\u4e0b\u4e00\u91cc\u7a0b\u7891",)),
        ),
        "audit": (
            (("scope",), ("\u8303\u56f4",)),
            (("period",), ("\u5468\u671f", "\u671f\u95f4")),
            (("evidence",), ("\u8bc1\u636e",)),
            (("findings",), ("\u53d1\u73b0",)),
            (("risk",), ("\u98ce\u9669",)),
            (("remediation_status", "remediation status"), ("\u6574\u6539\u72b6\u6001",)),
        ),
        "experiment": (
            (("hypothesis",), ("\u5047\u8bbe",)),
            (("sample",), ("\u6837\u672c",)),
            (("metric_definition", "metric definition"), ("\u6307\u6807\u5b9a\u4e49",)),
            (("results",), ("\u7ed3\u679c",)),
            (("uncertainty",), ("\u4e0d\u786e\u5b9a\u6027",)),
            (("conclusion",), ("\u7ed3\u8bba",)),
        ),
        "support": (
            (("issue",), ("\u95ee\u9898",)),
            (("customer_impact", "customer impact"), ("\u5ba2\u6237\u5f71\u54cd",)),
            (("handling_or_result", "handling or result"), ("\u5904\u7406\u6216\u7ed3\u679c",)),
            (("evidence",), ("\u8bc1\u636e",)),
            (("status",), ("\u72b6\u6001",)),
            (("follow_up", "follow-up", "follow up"), ("\u540e\u7eed",)),
        ),
    }
    DATE_FIELDS = {
        "release": (
            ("actual_delivery_date", "published_at", "effective_at"),
            ("\u5b9e\u9645\u4ea4\u4ed8\u65e5\u671f", "\u53d1\u5e03\u65f6\u95f4", "\u751f\u6548\u65f6\u95f4"),
        ),
        "engineering": (("occurred_at", "recorded_at"), ("\u53d1\u751f\u65f6\u95f4", "\u8bb0\u5f55\u65f6\u95f4")),
        "incident": (("occurred_at",), ("\u53d1\u751f\u65f6\u95f4",)),
        "decision": (("effective_at", "recorded_at"), ("\u751f\u6548\u65f6\u95f4", "\u8bb0\u5f55\u65f6\u95f4")),
        "project": (("updated_at", "recorded_at"), ("\u66f4\u65b0\u65f6\u95f4", "\u8bb0\u5f55\u65f6\u95f4")),
        "audit": (("recorded_at",), ("\u8bb0\u5f55\u65f6\u95f4",)),
        "experiment": (("occurred_at", "recorded_at"), ("\u53d1\u751f\u65f6\u95f4", "\u8bb0\u5f55\u65f6\u95f4")),
        "support": (("occurred_at", "updated_at"), ("\u53d1\u751f\u65f6\u95f4", "\u66f4\u65b0\u65f6\u95f4")),
    }

    def lint_module(self):
        return importlib.import_module("scripts.lint_log_draft")

    @staticmethod
    def issue_codes(issues):
        return {issue.code for issue in issues}

    def complete_draft(
        self,
        log_type,
        *,
        language="english",
        omitted_index=None,
        aliases_by_index=None,
        include_date=True,
        date_alias=None,
        date_value="2026-07-18T12:00:00+08:00",
        include_timezone=True,
        timezone_value="Asia/Shanghai",
    ):
        language_index = 0 if language == "english" else 1
        fields = [aliases[language_index][0] for aliases in self.APPROVED_FIELDS[log_type]]
        for index, alias in (aliases_by_index or {}).items():
            fields[index] = alias
        date_fields = {
            alias
            for language_aliases in self.DATE_FIELDS.values()
            for aliases in language_aliases
            for alias in aliases
        }
        sections = [
            (
                field,
                date_value
                if field in date_fields
                else timezone_value
                if log_type == "incident" and index == 2
                else "Recorded.",
            )
            for index, field in enumerate(fields)
            if index != omitted_index
            and not (
                log_type == "incident"
                and index == 2
                and not include_timezone
            )
        ]
        if include_timezone and log_type != "incident":
            sections.insert(
                0,
                ("timezone" if language == "english" else "\u65f6\u533a", timezone_value),
            )
        if include_date:
            sections.insert(
                0,
                (
                    date_alias or self.DATE_FIELDS[log_type][language_index][0],
                    date_value,
                ),
            )
        return "# Log\n\n" + "\n\n".join(
            f"## {field}\n\n{value}" for field, value in sections
        )

    def complete_chinese_draft(self, log_type):
        return self.complete_draft(log_type, language="chinese")

    def test_declares_exactly_the_eight_supported_log_types(self):
        lint = self.lint_module()

        self.assertEqual(
            {"audit", "decision", "engineering", "experiment", "incident", "project", "release", "support"},
            set(lint.SUPPORTED_LOG_TYPES),
        )

    def test_accepts_a_complete_approved_english_template_for_every_log_type(self):
        lint = self.lint_module()
        for log_type in lint.SUPPORTED_LOG_TYPES:
            with self.subTest(log_type=log_type):
                self.assertEqual([], lint.lint_draft(self.complete_draft(log_type), log_type=log_type))

    def test_opt_in_template_check_reports_its_suggested_fields(self):
        lint = self.lint_module()
        for log_type in lint.SUPPORTED_LOG_TYPES:
            with self.subTest(log_type=log_type):
                issues = lint.lint_draft("# Log\n\nDate: 2026-07-18\nTimezone: UTC", log_type=log_type, check_template=True)
                self.assertIn("missing_required_field", self.issue_codes(issues))
                self.assertTrue(all(issue.severity == "warning" for issue in issues))

    def test_accepts_a_complete_approved_chinese_template_for_every_log_type(self):
        lint = self.lint_module()
        for log_type in lint.SUPPORTED_LOG_TYPES:
            with self.subTest(log_type=log_type):
                self.assertEqual([], lint.lint_draft(self.complete_chinese_draft(log_type), log_type=log_type))

    def test_release_accepts_actual_delivery_date_as_the_delivery_field(self):
        lint = self.lint_module()
        draft = self.complete_draft(
            "release",
            language="chinese",
            aliases_by_index={0: "\u5b9e\u9645\u4ea4\u4ed8\u65e5\u671f"},
        )

        self.assertEqual([], lint.lint_draft(draft, log_type="release"))

    def test_template_advice_distinguishes_publication_from_delivery_labels(self):
        lint = self.lint_module()
        draft = self.complete_draft(
            "release",
            omitted_index=0,
            date_alias="published_at",
        )

        self.assertIn(
            "missing_required_field",
            self.issue_codes(lint.lint_draft(draft, log_type="release", check_template=True)),
        )

    def test_decision_and_project_accept_the_taxonomy_decision_label(self):
        lint = self.lint_module()
        for log_type, field_index in (("decision", 2), ("project", 4)):
            with self.subTest(log_type=log_type):
                draft = self.complete_draft(
                    log_type,
                    language="chinese",
                    aliases_by_index={field_index: "决定"},
                )
                self.assertEqual([], lint.lint_draft(draft, log_type=log_type))

    def test_accepts_every_approved_english_and_chinese_alias(self):
        lint = self.lint_module()
        for log_type, groups in self.APPROVED_FIELDS.items():
            for field_index, language_aliases in enumerate(groups):
                for language, aliases in zip(("english", "chinese"), language_aliases):
                    for alias in aliases:
                        with self.subTest(log_type=log_type, language=language, alias=alias):
                            draft = self.complete_draft(
                                log_type,
                                language=language,
                                aliases_by_index={field_index: alias},
                            )
                            self.assertEqual([], lint.lint_draft(draft, log_type=log_type, check_template=True))

    def test_accepts_every_exact_type_specific_date_alias(self):
        lint = self.lint_module()
        for log_type, language_aliases in self.DATE_FIELDS.items():
            for language, aliases in zip(("english", "chinese"), language_aliases):
                for alias in aliases:
                    with self.subTest(log_type=log_type, language=language, alias=alias):
                        draft = self.complete_draft(log_type, language=language, date_alias=alias)
                        self.assertEqual([], lint.lint_draft(draft, log_type=log_type))

    def test_opt_in_template_advice_recognizes_missing_labels_in_both_languages(self):
        lint = self.lint_module()
        for log_type, groups in self.APPROVED_FIELDS.items():
            for language in ("english", "chinese"):
                for omitted_index in range(len(groups)):
                    with self.subTest(log_type=log_type, language=language, omitted_index=omitted_index):
                        date_alias = None
                        if log_type == "release" and omitted_index == 0:
                            date_alias = "effective_at" if language == "english" else "\u751f\u6548\u65f6\u95f4"
                        issues = lint.lint_draft(
                            self.complete_draft(
                                log_type,
                                language=language,
                                omitted_index=omitted_index,
                                date_alias=date_alias,
                            ),
                            log_type=log_type, check_template=True,
                        )
                        self.assertIn("missing_required_field", self.issue_codes(issues))

    def test_opt_in_template_advice_does_not_infer_the_meaning_of_generic_dates(self):
        lint = self.lint_module()
        for log_type in self.DATE_FIELDS:
            with self.subTest(log_type=log_type):
                draft = self.complete_draft(log_type, include_date=False) + "\n\n## Date\n\n2026-07-18"
                codes = self.issue_codes(lint.lint_draft(draft, log_type=log_type, check_template=True))
                self.assertNotIn("missing_required_field", codes)
                self.assertIn("missing_date", codes)

    def test_empty_date_and_timezone_values_receive_review_warnings(self):
        lint = self.lint_module()
        for log_type in self.DATE_FIELDS:
            with self.subTest(log_type=log_type, field="date_nonsense"):
                codes = self.issue_codes(
                    lint.lint_draft(
                        self.complete_draft(log_type, date_value="sometime later"),
                        log_type=log_type,
                    )
                )
                self.assertIn("invalid_date", codes)
            with self.subTest(log_type=log_type, field="date_empty"):
                codes = self.issue_codes(
                    lint.lint_draft(
                        self.complete_draft(log_type, date_value=""),
                        log_type=log_type,
                    )
                )
                self.assertIn("invalid_date", codes)
            with self.subTest(log_type=log_type, field="timezone_invalid"):
                codes = self.issue_codes(
                    lint.lint_draft(
                        self.complete_draft(
                            log_type,
                            timezone_value="Mars/Olympus_Mons",
                        ),
                        log_type=log_type,
                    )
                )
                self.assertIn("invalid_timezone", codes)
            with self.subTest(log_type=log_type, field="timezone_empty"):
                codes = self.issue_codes(
                    lint.lint_draft(
                        self.complete_draft(log_type, timezone_value=""),
                        log_type=log_type,
                    )
                )
                self.assertIn("invalid_timezone", codes)
            with self.subTest(log_type=log_type, field="timezone_missing"):
                codes = self.issue_codes(
                    lint.lint_draft(
                        self.complete_draft(log_type, include_timezone=False),
                        log_type=log_type, check_template=True,
                    )
                )
                self.assertIn("missing_timezone", codes)

    def test_iso_dates_and_explicit_unknown_date_values_are_accepted(self):
        lint = self.lint_module()
        for log_type in self.DATE_FIELDS:
            for value in ("2026-07-18", "2026-07-18T12:00:00+08:00", "unknown"):
                with self.subTest(log_type=log_type, value=value):
                    issues = lint.lint_draft(
                        self.complete_draft(log_type, date_value=value),
                        log_type=log_type,
                    )
                    self.assertNotIn("invalid_date", self.issue_codes(issues))
            with self.subTest(log_type=log_type, value="\u5f85\u786e\u8ba4"):
                issues = lint.lint_draft(
                    self.complete_draft(
                        log_type,
                        language="chinese",
                        date_value="\u5f85\u786e\u8ba4",
                    ),
                    log_type=log_type,
                )
                self.assertNotIn("invalid_date", self.issue_codes(issues))

    def test_inline_and_heading_metadata_values_are_both_supported(self):
        lint = self.lint_module()
        draft = self.complete_draft("release")
        draft = draft.replace(
            "## actual_delivery_date\n\n2026-07-18T12:00:00+08:00",
            "actual_delivery_date: 2026-07-18",
        ).replace(
            "## timezone\n\nAsia/Shanghai",
            "timezone: Asia/Shanghai",
        )

        self.assertEqual([], lint.lint_draft(draft, log_type="release"))

    def test_reports_missing_applicable_date_and_timezone(self):
        lint = self.lint_module()

        issues = lint.lint_draft("# Incident\n\n## Summary\n\nService recovered.", log_type="incident", check_template=True)

        self.assertTrue({"missing_date", "missing_timezone"} <= self.issue_codes(issues))

    def test_reports_heading_level_jumps(self):
        lint = self.lint_module()

        issues = lint.lint_draft("# Project\n\n### Outcome\n\nRecorded.", log_type="project", style_advice=True)

        self.assertIn("heading_level_jump", self.issue_codes(issues))

    def test_rejects_an_unsupported_log_type(self):
        lint = self.lint_module()

        with self.assertRaises(ValueError):
            lint.lint_draft("# Weekly\n\nDate: 2026-07-18\nTimezone: Asia/Shanghai", log_type="weekly")

    def test_reports_status_that_is_expressed_by_color_only(self):
        lint = self.lint_module()

        issues = lint.lint_draft(
            "# Engineering\n\nDate: 2026-07-18\nTimezone: Asia/Shanghai\n\n"
            "<span style=\"color:red\">\u25cf</span>",
            log_type="engineering", style_advice=True,
        )

        self.assertIn("color_only_status", self.issue_codes(issues))

    def test_reports_customer_facing_internal_implementation_leakage(self):
        lint = self.lint_module()

        issues = lint.lint_draft(
            "# Release\n\nDate: 2026-07-18\nTimezone: Asia/Shanghai\n\n"
            "Customer update: we changed the internal /admin/v2/rollout endpoint.",
            log_type="release",
        )

        self.assertIn("internal_implementation_leak", self.issue_codes(issues))

    def test_reports_unsupported_statistical_significance_claims(self):
        lint = self.lint_module()

        issues = lint.lint_draft(
            "# Experiment\n\nDate: 2026-07-18\nTimezone: Asia/Shanghai\n\n"
            "The result is statistically significant.",
            log_type="experiment",
        )

        self.assertIn("unsupported_significance_claim", self.issue_codes(issues))

    def test_reports_obvious_secrets_and_images_without_text_fallback(self):
        lint = self.lint_module()

        issues = lint.lint_draft(
            "# Project\n\nDate: 2026-07-18\nTimezone: Asia/Shanghai\n\n"
            "Token: sk-live-12345678901234567890\n\n![](chart.png)",
            log_type="project", style_advice=True,
        )

        self.assertTrue({"obvious_secret", "missing_visual_caption"} <= self.issue_codes(issues))

    def test_reports_password_cookie_session_bearer_and_api_key_secrets(self):
        lint = self.lint_module()
        fragments = (
            "pass" + "word: correct-horse-battery-staple",
            "pass" + "word=x",
            "coo" + "kie=session-cookie-value",
            "coo" + "kie=id",
            "sess" + "ion: private-session-value",
            "sess" + "ion=x",
            "Authorization: " + "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
            "api" + "_key=private-api-key-value",
        )
        for fragment in fragments:
            with self.subTest(fragment=fragment):
                issues = lint.lint_draft(self.complete_draft("engineering") + "\n\n" + fragment, log_type="engineering")
                self.assertIn("obvious_secret", self.issue_codes(issues))

    def test_reports_email_and_common_phone_as_possible_pii_in_text_and_images(self):
        lint = self.lint_module()
        fragments = (
            "Contact person@example.com for details.",
            "Call 13800138000 for escalation.",
            "Call 138-0013-8000 for escalation.",
            "Call +1 (415) 555-2671 for escalation.",
            "Call +44 20 7946 0958 for escalation.",
            "![Owner person@example.com](owner.png)",
            "![Escalation contact](https://example.invalid/138-0013-8000.png)",
        )
        for fragment in fragments:
            with self.subTest(fragment=fragment):
                issues = lint.lint_draft(self.complete_draft("support") + "\n\n" + fragment, log_type="support")
                self.assertIn("possible_pii", self.issue_codes(issues))

    def test_ordinary_dates_do_not_trigger_possible_pii(self):
        lint = self.lint_module()
        draft = (
            self.complete_draft("project")
            + "\n\nPeriod: 2026-07-18 through 2026-07-20. "
            + "Snapshot: 2026/07/18. Timestamp: 2026-07-18T12:00:00+08:00."
        )
        self.assertNotIn("possible_pii", self.issue_codes(lint.lint_draft(draft, log_type="project")))

    def test_reports_silent_history_rewrite_language_for_sensitive_log_types(self):
        lint = self.lint_module()

        for log_type in ("incident", "audit", "decision"):
            with self.subTest(log_type=log_type):
                issues = lint.lint_draft(
                    "# Record\n\nDate: 2026-07-18\nTimezone: Asia/Shanghai\n\n"
                    "We rewrote the earlier entry so the previous record is no longer visible.",
                    log_type=log_type,
                )
                self.assertIn("silent_history_rewrite", self.issue_codes(issues))

    def test_does_not_apply_silent_history_rewrite_rule_to_release_or_project_logs(self):
        lint = self.lint_module()

        for log_type in ("release", "project"):
            with self.subTest(log_type=log_type):
                issues = lint.lint_draft(
                    "# Record\n\nDate: 2026-07-18\nTimezone: Asia/Shanghai\n\n"
                    "We rewrote the earlier entry so the previous record is no longer visible.",
                    log_type=log_type,
                )
                self.assertNotIn("silent_history_rewrite", self.issue_codes(issues))

    def test_recognizes_chinese_date_timezone_and_significance_claims(self):
        lint = self.lint_module()
        issues = lint.lint_draft(
            "# 实验\n\n记录时间：2026-07-18\n\n## 假设\nA\n## 方法\nB\n## 结果\nC\n## 证据\nD\n## 限制\nE\n\n结果具有统计显著性。",
            log_type="experiment",
        )

        codes = self.issue_codes(issues)
        self.assertNotIn("missing_date", codes)
        self.assertIn("unsupported_significance_claim", codes)

    def test_reports_color_only_emoji_statuses_without_text(self):
        lint = self.lint_module()
        for emoji in ("\U0001f534", "\U0001f7e1", "\U0001f7e2"):
            with self.subTest(emoji=emoji):
                issues = lint.lint_draft(f"# Log\n\nDate: 2026-07-18\nTimezone: UTC\n\n{emoji}", log_type="engineering", style_advice=True)
                self.assertIn("color_only_status", self.issue_codes(issues))

    def test_reports_html_image_without_alt_and_customer_implementation_terms(self):
        lint = self.lint_module()
        issues = lint.lint_draft(
            "# Release\n\nDate: 2026-07-18\nTimezone: UTC\n\n"
            "Customer update: MobileCategoryPanel uses Vitest and a SQL query. <img src=\"chart.png\">",
            log_type="release", style_advice=True,
        )
        self.assertTrue({"missing_visual_caption", "internal_implementation_leak"} <= self.issue_codes(issues))

    def test_reports_chinese_significant_improvement_without_evidence(self):
        lint = self.lint_module()
        issues = lint.lint_draft("# 实验\n\n日期：2026-07-18\n时区：UTC\n\n结果显著提升。", log_type="experiment")
        self.assertIn("unsupported_significance_claim", self.issue_codes(issues))

    def test_reports_wide_tables_incomplete_chart_context_and_whiteboard_without_text_equivalent(self):
        lint = self.lint_module()
        issues = lint.lint_draft(
            self.complete_draft("project") + "\n\n|a|b|c|d|e|f|g|\n|-|-|-|-|-|-|-|\n|1|2|3|4|5|6|7|\n\n"
            "![Trend](chart.png)\n\nWhiteboard: [diagram](board-link)",
            log_type="project", style_advice=True,
        )
        self.assertTrue({"wide_table", "incomplete_visual_context", "missing_whiteboard_text"} <= self.issue_codes(issues))

    def test_plain_image_does_not_require_chart_range_unit_or_source(self):
        lint = self.lint_module()
        for image in (
            "![Team photo](photo.png)",
            '<img alt="Team photo" src="photo.png">',
        ):
            with self.subTest(image=image):
                issues = lint.lint_draft(
                    self.complete_draft("project") + "\n\n" + image,
                    log_type="project", style_advice=True,
                )
                self.assertNotIn("incomplete_visual_context", self.issue_codes(issues))

    def test_chart_detection_uses_markdown_alt_and_url_plus_html_alt_and_src(self):
        lint = self.lint_module()
        charts = (
            "![Metrics](weekly-trend.png)",
            "![Trend](metrics.png)",
            '<img alt="Metrics" src="weekly-trend.png">',
            '<img alt="Trend chart" src="metrics.png">',
        )
        for chart in charts:
            with self.subTest(chart=chart):
                issues = lint.lint_draft(
                    self.complete_draft("project") + "\n\n" + chart,
                    log_type="project", style_advice=True,
                )
                self.assertIn(
                    "incomplete_visual_context",
                    self.issue_codes(issues),
                )

    def test_opt_in_chinese_visual_advice_suggests_context_and_equivalent_text(self):
        lint = self.lint_module()
        issues = lint.lint_draft(
            self.complete_chinese_draft("project") + "\n\n![\u8d8b\u52bf\u56fe\u8868](chart.png)\n\n\u767d\u677f\uff1a[\u67b6\u6784](board-link)",
            log_type="project", style_advice=True,
        )
        self.assertTrue({"incomplete_visual_context", "missing_whiteboard_text"} <= self.issue_codes(issues))

    def test_default_profile_still_selects_full_without_forcing_a_template(self):
        lint = self.lint_module()
        for log_type in lint.SUPPORTED_LOG_TYPES:
            for draft in (self.complete_draft(log_type), "### 分类显示\n\n优化了分类图标显示。"):
                with self.subTest(log_type=log_type, draft=draft):
                    self.assertEqual(
                        lint.lint_draft(draft, log_type=log_type),
                        lint.lint_draft(draft, log_type=log_type, profile="full"),
                    )

    def test_append_accepts_a_short_fragment_without_repeating_document_metadata(self):
        lint = self.lint_module()
        draft = "### 分类显示\n\n优化了分类图标显示；缺少图标时保留原有封面回退。"
        for log_type in lint.SUPPORTED_LOG_TYPES:
            with self.subTest(log_type=log_type):
                self.assertEqual([], lint.lint_draft(draft, log_type=log_type, profile="append"))
                self.assertEqual([], lint.lint_draft(draft, log_type=log_type))

    def test_append_rejects_empty_or_whitespace_only_text(self):
        lint = self.lint_module()
        for draft in ("", " \t\r\n"):
            with self.subTest(draft=draft):
                self.assertEqual(
                    {"empty_draft"},
                    self.issue_codes(lint.lint_draft(draft, log_type="release", profile="append")),
                )

    def test_append_does_not_infer_metadata_from_dates_or_prose(self):
        lint = self.lint_module()
        draft = (
            "## 2026-09-07\n\n分类显示优化。发布时间尚待确认，沿用原章节日期口径。\n\n"
            "Date: this is an ordinary section label\n\n日期：尚待核验\n\n"
            "The recorded_at value will be checked separately."
        )
        self.assertEqual([], lint.lint_draft(draft, log_type="release", profile="append"))

    def test_all_known_explicit_dates_are_checked_for_every_type_and_profile(self):
        lint = self.lint_module()
        aliases = {
            alias
            for language_aliases in self.DATE_FIELDS.values()
            for date_aliases in language_aliases
            for alias in date_aliases
        }
        for profile in ("full", "append"):
            for log_type in lint.SUPPORTED_LOG_TYPES:
                base = self.complete_draft(log_type) if profile == "full" else "分类显示优化。"
                for alias in aliases:
                    for field in (f"{alias}: tomorrow", f"## {alias}\n\ntomorrow"):
                        with self.subTest(profile=profile, log_type=log_type, field=field):
                            codes = self.issue_codes(lint.lint_draft(
                                base + "\n\n" + field, log_type=log_type, profile=profile,
                            ))
                            self.assertIn("invalid_date", codes)

    def test_valid_or_unknown_dates_do_not_mask_an_invalid_explicit_date(self):
        lint = self.lint_module()
        for profile in ("full", "append"):
            base = self.complete_draft("release") if profile == "full" else "分类显示优化。"
            for valid_value in ("2026-09-07", "unknown", "待确认"):
                for invalid_value in ("", "2026-99-99"):
                    fields = [f"recorded_at: {valid_value}", f"recorded_at: {invalid_value}"]
                    for ordered_fields in (fields, fields[::-1]):
                        with self.subTest(profile=profile, fields=ordered_fields):
                            codes = self.issue_codes(lint.lint_draft(
                                base + "\n\n" + "\n".join(ordered_fields),
                                log_type="release", profile=profile,
                            ))
                            self.assertIn("invalid_date", codes)

    def test_every_explicit_timezone_is_checked_in_both_profiles(self):
        lint = self.lint_module()
        for profile in ("full", "append"):
            base = self.complete_draft("release") if profile == "full" else "分类显示优化。"
            for alias in ("timezone", "time zone", "时区"):
                for invalid_value in ("", "Mars/Olympus_Mons"):
                    fields = [f"{alias}: UTC", f"{alias}: {invalid_value}"]
                    for ordered_fields in (fields, fields[::-1]):
                        with self.subTest(profile=profile, fields=ordered_fields):
                            codes = self.issue_codes(lint.lint_draft(
                                base + "\n\n" + "\n".join(ordered_fields),
                                log_type="release", profile=profile,
                            ))
                            self.assertIn("invalid_timezone", codes)

    def test_append_accepts_valid_explicit_time_metadata(self):
        lint = self.lint_module()
        for value in ("2026-09-07", "2026-09-07T12:00:00+08:00", "unknown", "待确认"):
            with self.subTest(value=value):
                self.assertEqual([], lint.lint_draft(
                    f"分类显示优化。\n\n记录时间：{value}\n时区：Asia/Shanghai",
                    log_type="release", profile="append",
                ))

    def test_append_keeps_shared_content_and_visual_checks(self):
        lint = self.lint_module()
        cases = {
            "heading_level_jump": "# Log\n\n### Change\n\nRecorded.",
            "color_only_status": "\U0001f7e2",
            "internal_implementation_leak": "Customer update: changed the internal /admin endpoint.",
            "unsupported_significance_claim": "结果显著提升。",
            "obvious_secret": "pass" + "word=x",
            "possible_pii": "Contact person@example.com.",
            "missing_visual_caption": "![](photo.png)",
            "wide_table": "|a|b|c|d|e|f|g|",
            "incomplete_visual_context": "![Trend](chart.png)",
            "missing_whiteboard_text": "Whiteboard: [diagram](board-link)",
        }
        for code, fragment in cases.items():
            with self.subTest(code=code):
                self.assertIn(code, self.issue_codes(lint.lint_draft(
                    fragment, log_type="release", profile="append", style_advice=True,
                )))

    def test_append_keeps_sensitive_history_checks(self):
        lint = self.lint_module()
        for log_type in ("incident", "audit", "decision"):
            with self.subTest(log_type=log_type):
                self.assertIn("silent_history_rewrite", self.issue_codes(lint.lint_draft(
                    "We rewrote the earlier entry so the previous record is no longer visible.",
                    log_type=log_type, profile="append",
                )))

    def test_rejects_an_unsupported_profile(self):
        lint = self.lint_module()
        with self.assertRaisesRegex(ValueError, "unsupported lint profile"):
            lint.lint_draft("分类显示优化。", log_type="release", profile="quick")

    def test_cli_profiles_report_issue_severity_and_matching_exit_status(self):
        lint = self.lint_module()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "draft.md"
            base_args = ["--log-type", "release", "--file", str(path)]
            cases = (
                (self.complete_draft("release"), [], 0),
                (self.complete_draft("release"), ["--profile", "full"], 0),
                ("分类显示优化。", [], 0),
                ("分类显示优化。", ["--profile", "full"], 0),
                ("分类显示优化。", ["--profile", "append"], 0),
                ("分类显示优化。", ["--check-template"], 0),
                ("分类显示优化。", ["--profile", "append", "--check-template"], 2),
                ("# A\n\n### B\n\n说明。", ["--style-advice"], 0),
                ("记录时间：2026.9.7\n时区：UTC+8\n说明。", [], 0),
                ("Contact public@example.com", [], 0),
                ("pass" + "word=x", [], 1),
                ("pass" + "word=x", ["--style-advice", "--check-template"], 1),
                (" ", [], 1),
                (" ", ["--profile", "append"], 1),
                ("分类显示优化。", ["--profile", "quick"], 2),
            )
            for draft, options, expected_status in cases:
                with self.subTest(draft=draft, options=options):
                    path.write_text(draft, encoding="utf-8")
                    output = io.StringIO()
                    with redirect_stdout(output):
                        status = lint.main(base_args + options)
                    result = json.loads(output.getvalue())
                    self.assertEqual(expected_status, status)
                    self.assertEqual(expected_status == 0, result["ok"])
                    self.assertEqual(
                        {"ok", "error"} if expected_status == 2 else {"ok", "issues"},
                        set(result),
                    )
                    for issue in result.get("issues", []):
                        self.assertEqual({"code", "message", "severity"}, set(issue))
                    if expected_status != 2:
                        self.assertEqual(
                            expected_status == 1,
                            any(issue["severity"] == "error" for issue in result["issues"]),
                        )

    def test_distinct_complete_document_styles_need_no_template_labels(self):
        lint = self.lint_module()
        drafts = {
            "release": "**2026.9.7 · 商品管理**\n\n分类图标现可正常显示，便于区分类目。验证完成，尚未正式发布。",
            "incident": "# 服务恢复记录\n\n10:00 接口开始超时。10:08 回滚后恢复。影响持续八分钟，根因仍在调查。",
            "experiment": "# 本轮观察\n\n在相同请求集上比较两种配置，响应时间差异较小。样本只覆盖低负载，不外推到高峰。下一轮扩大负载范围。",
        }
        for log_type, draft in drafts.items():
            with self.subTest(log_type=log_type):
                self.assertEqual([], lint.lint_draft(draft, log_type=log_type))

    def test_layout_advice_is_opt_in_and_never_blocks_document_style(self):
        lint = self.lint_module()
        draft = "# A\n\n### B\n\n|a|b|c|d|e|f|g|\n\n![](decoration.png)\n\n\U0001f7e2"
        self.assertEqual([], lint.lint_draft(draft, log_type="project"))
        issues = lint.lint_draft(draft, log_type="project", style_advice=True)
        self.assertTrue({"wide_table", "heading_level_jump", "missing_visual_caption", "color_only_status"} <= self.issue_codes(issues))
        self.assertTrue(all(issue.severity == "warning" for issue in issues))

    def test_keywords_and_non_iso_display_dates_request_review_not_rewriting(self):
        lint = self.lint_module()
        draft = (
            "记录时间：2026.9.7\n时区：UTC+8\n\n"
            "尚未证明具有统计显著性。Contact public@example.com。\n"
            "未重写旧记录，也未删除历史。Customer update: SQL export is available."
        )
        issues = lint.lint_draft(draft, log_type="audit")
        self.assertTrue({"invalid_date", "invalid_timezone", "possible_pii", "unsupported_significance_claim", "silent_history_rewrite", "internal_implementation_leak"} <= self.issue_codes(issues))
        self.assertTrue(all(issue.severity == "warning" for issue in issues))

    def test_secret_and_empty_errors_are_independent_of_advice_flags(self):
        lint = self.lint_module()
        for profile in ("full", "append"):
            for style_advice in (False, True):
                for fragment, code in ((" \t\n", "empty_draft"), ("pass" + "word=x", "obvious_secret")):
                    with self.subTest(profile=profile, advice=style_advice, code=code):
                        issues = lint.lint_draft(fragment, log_type="release", profile=profile, style_advice=style_advice)
                        self.assertIn(code, {issue.code for issue in issues if issue.severity == "error"})


if __name__ == "__main__":
    unittest.main()
