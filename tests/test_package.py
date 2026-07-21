import importlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class PackageValidationContractTests(unittest.TestCase):
    AGENT_METADATA = (
        "interface:\n"
        "  display_name: \"\u98de\u4e66\u4e91\u65e5\u5fd7\"\n"
        "  short_description: \"\u5c06\u6280\u672f\u4e0e\u4e1a\u52a1\u53d8\u5316\u6574\u7406\u4e3a\u4e13\u4e1a\u3001\u53ef\u9a8c\u8bc1\u7684\u98de\u4e66\u4e91\u6587\u6863\u65e5\u5fd7\"\n"
        "  default_prompt: \"Use $feishu-cloud-logs to draft or safely maintain a professional Feishu cloud-document log.\"\n"
        "policy:\n"
        "  allow_implicit_invocation: true\n"
    )
    REQUIRED_SCRIPTS = (
        "resolve_runtime.py",
        "private_memory.py",
        "lint_log_draft.py",
        "validate_package.py",
    )
    REQUIRED_REFERENCES = (
        "log-taxonomy.md",
        "templates.md",
        "writing-quality.md",
        "layout-and-visuals.md",
        "private-memory-contract.md",
        "execution-and-safety.md",
    )

    def package(self):
        return importlib.import_module("scripts.validate_package")

    def write_file(self, root, relative_path, content):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def tracked_manifest(self, root):
        ignored = {".git", ".local", ".superpowers", "dist", "__pycache__"}
        return sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and not ignored.intersection(path.relative_to(root).parts)
        )

    def skill_document(self, references=None):
        references = self.REQUIRED_REFERENCES if references is None else references
        reference_links = "\n".join(
            f"- [{reference}](references/{reference})" for reference in references
        )
        return (
            "---\n"
            "name: feishu-cloud-logs\n"
            "description: Feishu cloud-document log safety workflow.\n"
            "---\n\n"
            "# \u98de\u4e66\u4e91\u65e5\u5fd7\n\n"
            "Use the referenced safety workflow.\n\n"
            "## References\n\n"
            f"{reference_links}\n"
        )

    def make_valid_package(self, root):
        self.write_file(root, ".gitignore", "/.local/\n/dist/\n/__pycache__/\n*.pyc\n/.superpowers/\n")
        self.write_file(
            root,
            "compatibility.json",
            '{"version": 1, "policy": "capability-gated", "package": "@larksuite/' + 'cli", "tested_versions": ["2.3.1"], "pinned_fallback": "@larksuite/' + 'cli@2.3.1", "required_skills": ["docs"], "required_capabilities": ["docs.read"]}\n',
        )
        self.write_file(
            root,
            "SKILL.md",
            self.skill_document(),
        )
        self.write_file(
            root,
            "README.md",
            "# \u98de\u4e66\u4e91\u65e5\u5fd7\n\n"
            "\u7528\u4e8e\u5b89\u5168\u7ef4\u62a4\u98de\u4e66\u4e91\u6587\u6863\u65e5\u5fd7\u3002\n\n"
            "## English summary\n\n"
            "Safe Feishu cloud-document logging.\n",
        )
        self.write_file(
            root,
            "agents/openai.yaml",
            self.AGENT_METADATA,
        )
        for script in self.REQUIRED_SCRIPTS:
            self.write_file(root, f"scripts/{script}", "# implementation placeholder\n")
        for reference in self.REQUIRED_REFERENCES:
            self.write_file(root, f"references/{reference}", "# Reference\n")
        for test in ("test_runtime.py", "test_private_memory.py", "test_draft_lint.py", "test_package.py"):
            self.write_file(root, f"tests/{test}", "# contract\n")
        self.write_file(root, ".github/workflows/validate.yml", "name: validate\n")

    def validate(self, root, tracked_paths=None, mode="package", allow_local=False):
        package = self.package()
        return package.validate_package(root, tracked_paths=tracked_paths, mode=mode, allow_local=allow_local)

    def test_accepts_a_complete_public_package_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)

            report = self.validate(root)

        self.assertEqual([], report.errors)

    def test_rejects_nested_skill_layout_and_missing_required_files_or_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            (root / "SKILL.md").unlink()
            self.write_file(root, "skills/feishu-cloud-logs/SKILL.md", "# nested\n")
            (root / "references/execution-and-safety.md").unlink()

            report = self.validate(root)

        self.assertTrue({"missing_root_skill", "nested_skill_layout", "missing_required_file"} <= {e.code for e in report.errors})

    def test_requires_each_approved_script_by_its_exact_filename(self):
        for script in self.REQUIRED_SCRIPTS:
            with self.subTest(script=script), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.make_valid_package(root)
                (root / "scripts" / script).unlink()
                self.write_file(root, "scripts/replacement.py", "# not an approved script\n")

                report = self.validate(root)

                self.assertIn("missing_required_file", {error.code for error in report.errors})

    def test_requires_each_approved_reference_by_its_exact_filename_without_substitution(self):
        for reference in self.REQUIRED_REFERENCES:
            with self.subTest(reference=reference), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.make_valid_package(root)
                (root / "references" / reference).unlink()
                self.write_file(root, "references/replacement.md", "# Not an approved reference\n")

                report = self.validate(root)

                self.assertIn("missing_required_file", {error.code for error in report.errors})

    def test_requires_skill_to_link_every_approved_reference(self):
        for omitted_reference in self.REQUIRED_REFERENCES:
            with self.subTest(omitted_reference=omitted_reference), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.make_valid_package(root)
                references = tuple(
                    reference for reference in self.REQUIRED_REFERENCES if reference != omitted_reference
                )
                self.write_file(root, "SKILL.md", self.skill_document(references))

                report = self.validate(root)

                self.assertIn("missing_reference_link", {error.code for error in report.errors})

    def test_rejects_extra_public_scripts_and_references_even_when_skill_links_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(root, "scripts/extra_public_tool.py", "# Not an approved script\n")
            self.write_file(root, "references/extra-public-reference.md", "# Not an approved reference\n")
            self.write_file(
                root,
                "SKILL.md",
                self.skill_document(self.REQUIRED_REFERENCES + ("extra-public-reference.md",)),
            )

            report = self.validate(root)

        error_codes = {error.code for error in report.errors}
        self.assertIn("unapproved_script", error_codes)
        self.assertIn("unapproved_reference", error_codes)

    def test_rejects_an_unapproved_public_top_level_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(root, "extra-public-file.md", "Not an approved package entry.\n")

            report = self.validate(root)

        self.assertIn("unapproved_top_level", {error.code for error in report.errors})

    def test_rejects_mismatched_metadata_and_a_non_chinese_first_readme(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(root, "agents/openai.yaml", "interface:\n  display_name: Another skill\n")
            self.write_file(root, "README.md", "# Feishu Cloud Logs\n\nEnglish first.\n")

            report = self.validate(root)

        self.assertTrue({"metadata_mismatch", "invalid_agent_schema", "readme_not_chinese_first", "readme_missing_english_summary"} <= {e.code for e in report.errors})

    def test_agent_metadata_rejects_comments_extra_keys_and_dependency_hints(self):
        extras = (
            "# comment\n",
            "icon_small: icon.svg\n",
            "brand_color: '#ffffff'\n",
            "dependencies:\n  tools: [legacy]\n",
        )
        for extra in extras:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.make_valid_package(root)
                self.write_file(root, "agents/openai.yaml", self.AGENT_METADATA + extra)

                report = self.validate(root)

                self.assertIn("invalid_agent_schema", {error.code for error in report.errors})

    def test_rejects_public_private_terms_identifiers_legacy_mcp_and_unpinned_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(
                root,
                "references/execution-and-safety.md",
                "private-" + "project-omega " + "dox" + "cn0123456789abcdef " + "mcp" + "__legacy_feishu @larksuite/" + "cli@" + "latest\n",
            )

            report = self.validate(root)

        self.assertTrue(
            {"private_" + "project_term", "feishu_identifier", "legacy_mcp_name", "unpinned_cli"}
            <= {e.code for e in report.errors}
        )

    def test_rejects_local_content_license_and_an_overlong_skill_body(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(root, ".local/index.json", "{}")
            self.write_file(root, "LICENSE", "license text\n")
            self.write_file(root, "SKILL.md", "---\nname: feishu-cloud-logs\n---\n\n" + ("word " * 6000))

            report = self.validate(root)

        self.assertTrue({"local_content_in_package", "license_not_allowed", "skill_body_too_long"} <= {e.code for e in report.errors})

    def test_scans_tracked_public_files_without_scanning_an_ignored_local_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(root, ".local/index.json", "dox" + "cn0123456789abcdef\n")

            report = self.validate(root, tracked_paths=self.tracked_manifest(root), mode="source")

        self.assertEqual([], report.errors)

    def test_requires_a_capability_gated_compatibility_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            (root / "compatibility.json").unlink()
            missing = self.validate(root)
            self.assertIn("missing_required_file", {error.code for error in missing.errors})

            self.write_file(root, "compatibility.json", '{"version": 1, "policy": "mutable", "package": "@larksuite/' + 'cli", "tested_versions": [], "pinned_fallback": "@larksuite/' + 'cli@' + 'latest", "required_skills": [], "required_capabilities": []}')
            invalid = self.validate(root)
            self.assertIn("invalid_compatibility_schema", {error.code for error in invalid.errors})

            self.write_file(
                root,
                "compatibility.json",
                '{"version": true, "policy": "capability-gated", "package": "@larksuite/'
                + 'cli", "tested_versions": ["2.3.1"], "pinned_fallback": "@larksuite/'
                + 'cli@2.3.1", "required_skills": ["docs"], "required_capabilities": ["docs.read"]}',
            )
            boolean_version = self.validate(root)
            self.assertIn(
                "invalid_compatibility_schema",
                {error.code for error in boolean_version.errors},
            )

    def test_rejects_nested_public_content_and_any_nonroot_skill(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(root, "scripts/nested/tool.py", "# no\n")
            self.write_file(root, "docs/SKILL.md", "# nested\n")

            report = self.validate(root)

        self.assertTrue({"unapproved_script", "nested_skill_layout"} <= {error.code for error in report.errors})

    def test_rejects_unversioned_mutable_cli_and_known_private_leaks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(
                root,
                "references/execution-and-safety.md",
                "npx @larksuite/" + "cli; npx @larksuite/" + "cli@" + "next " + "go" + "shop " + "yuko" + "cart get_" + "feishu_document_by_id create_" + "feishu_document " + "dox" + "cn" + ("a" * 180),
            )

            report = self.validate(root)

        self.assertTrue({"unpinned_cli", "private_" + "project_term", "feishu_identifier", "legacy_mcp_name"} <= {error.code for error in report.errors})

    def test_requires_workflow_exact_ignore_rules_and_rejects_tracked_private_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            (root / ".github/workflows/validate.yml").unlink()
            self.write_file(root, ".gitignore", ".local/\n")
            self.write_file(root, ".local/index.json", "{}")
            report = self.validate(
                root,
                tracked_paths=[*self.tracked_manifest(root), ".local/index.json"],
                mode="source",
            )

        self.assertTrue({"missing_required_file", "invalid_gitignore", "tracked_private_path"} <= {error.code for error in report.errors})

    def test_gitignore_requires_the_superpowers_exclusion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(root, ".gitignore", "/.local/\n/dist/\n/__pycache__/\n*.pyc\n")

            report = self.validate(root)

        self.assertIn("invalid_gitignore", {error.code for error in report.errors})

    def test_scans_compatibility_for_leaks_but_not_its_structured_package_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(root, "compatibility.json", '{"version":1,"policy":"capability-gated","package":"@larksuite/' + 'cli","tested_versions":["2.3.1"],"pinned_fallback":"@larksuite/' + 'cli@2.3.1","required_skills":["docs"],"required_capabilities":["docs.read"],"note":"' + "go" + "shop" + '"}')
            report = self.validate(root)

        self.assertIn("private_" + "project_term", {error.code for error in report.errors})

    def test_reports_malformed_public_utf8_without_raising(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            (root / "agents/openai.yaml").write_bytes(b"\xff\xfe")

            report = self.validate(root)

        self.assertIn("invalid_public_encoding", {error.code for error in report.errors})

    def test_package_rejects_local_while_installed_and_explicit_allow_local_prune_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            private_path = root / ".local/index.json"
            private_path.parent.mkdir(parents=True)
            private_path.write_bytes(b"\xff\xfe private")

            self.assertIn("local_content_in_package", {error.code for error in self.validate(root).errors})
            original_read_text = Path.read_text

            def reject_private_read(path, *args, **kwargs):
                if ".local" in Path(path).parts:
                    raise AssertionError("installed validation read private-local content")
                return original_read_text(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", reject_private_read):
                installed = self.validate(root, mode="installed")
            self.assertEqual([], installed.errors)
            self.assertNotIn("local_content_in_package", {error.code for error in self.validate(root, allow_local=True).errors})

    def test_source_mode_fails_when_the_tracked_manifest_is_unavailable(self):
        package = self.package()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            with mock.patch.object(package, "_tracked_paths", return_value=None):
                report = package.validate_package(root, mode="source")

        self.assertIn("tracked_manifest_unavailable", {error.code for error in report.errors})

    def test_source_mode_requires_every_required_file_in_the_normalized_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            manifest = self.tracked_manifest(root)
            manifest.remove("README.md")

            report = self.validate(root, tracked_paths=manifest, mode="source")

        self.assertIn("missing_tracked_required_file", {error.code for error in report.errors})

    def test_rejects_contextual_modern_target_tokens_in_public_files(self):
        fields = (
            "document_" + "token",
            "wiki_node_" + "token",
            "canonical_" + "url",
            "target_" + "token",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            opaque = "ModernOpaque" + "12345678"
            self.write_file(
                root,
                "references/execution-and-safety.md",
                "\n".join(field + ": " + opaque for field in fields),
            )

            report = self.validate(root)

        self.assertIn("feishu_identifier", {error.code for error in report.errors})

    def test_rejects_contextual_modern_target_tokens_in_quoted_json_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            opaque = "OnlyLettersOpaqueTarget"
            field = "document_" + "token"
            self.write_file(
                root,
                "references/execution-and-safety.md",
                '{"' + field + '": "' + opaque + '"}',
            )

            report = self.validate(root)

        self.assertIn("feishu_identifier", {error.code for error in report.errors})

    def test_unquoted_only_letter_target_values_are_contextual_but_field_prose_is_not(self):
        field = "document_" + "token"
        for opaque in ("OnlyLets", "OnlyLetterValue", "OnlyLettersOpaqueTarget"):
            with self.subTest(opaque=opaque), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.make_valid_package(root)
                self.write_file(
                    root,
                    "references/execution-and-safety.md",
                    field + ": " + opaque,
                )

                report = self.validate(root)

            self.assertIn("feishu_identifier", {error.code for error in report.errors})

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(
                root,
                "references/execution-and-safety.md",
                "The public contract defines " + field + " as a private target field.",
            )

            report = self.validate(root)

        self.assertNotIn("feishu_identifier", {error.code for error in report.errors})

    def test_malformed_compatibility_utf8_is_reported_without_raising(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            (root / "compatibility.json").write_bytes(b"\xff\xfe")

            report = self.validate(root)

        self.assertTrue(
            {"invalid_compatibility_schema", "invalid_public_encoding"}
            <= {error.code for error in report.errors}
        )

    def test_unapproved_fixture_files_are_not_part_of_the_public_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(root, "tests/fixtures/unapproved.json", "{}")

            report = self.validate(root)

        self.assertIn("unapproved_public_file", {error.code for error in report.errors})

    def test_ignored_dist_may_exist_at_top_level_without_being_scanned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(
                root,
                "dist/private-artifact.txt",
                "private-" + "project https://sample." + "larksuite.com/docx/TargetLike123",
            )

            report = self.validate(root)

        self.assertEqual([], report.errors)

    def test_detects_larksuite_document_urls_and_cli_usage_errors_are_json(self):
        package = self.package()
        from contextlib import redirect_stdout
        from io import StringIO
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_valid_package(root)
            self.write_file(root, "references/execution-and-safety.md", "https://sample." + "larksuite.com/wiki/" + "WikiTarget123")
            report = self.validate(root)
        self.assertIn("feishu_identifier", {error.code for error in report.errors})

        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(2, package.main(["--mode", "invalid"]))
        self.assertFalse(__import__("json").loads(output.getvalue())["ok"])


class RealPublicPackageContractTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]
    LOG_TYPES = (
        "release",
        "engineering",
        "project",
        "incident",
        "decision",
        "experiment",
        "audit",
        "support",
    )
    REQUIRED_REFERENCES = PackageValidationContractTests.REQUIRED_REFERENCES
    EXPECTED_COMPATIBILITY = {
        "version": 1,
        "policy": "capability-gated",
        "package": "@larksuite/" + "cli",
        "tested_versions": ["1.0.72"],
        "pinned_fallback": "@larksuite/" + "cli@1.0.72",
        "required_skills": ["lark-shared", "lark-doc", "lark-wiki"],
        "required_capabilities": [
            "skills.read",
            "docs.create.v2",
            "docs.fetch.v2",
            "docs.update.v2",
            "wiki.resolve",
            "risk.confirmation",
        ],
    }

    @classmethod
    def read(cls, relative_path):
        return (cls.ROOT / relative_path).read_text(encoding="utf-8")

    def test_root_skill_has_only_approved_frontmatter_and_a_concise_body(self):
        content = self.read("SKILL.md")
        match = re.match(r"\A---\n(.*?)\n---\n", content, re.S)
        self.assertIsNotNone(match)
        frontmatter_keys = [
            line.split(":", 1)[0].strip()
            for line in match.group(1).splitlines()
            if line.strip()
        ]
        self.assertEqual(["name", "description"], frontmatter_keys)
        self.assertIn("name: feishu-cloud-logs", match.group(1))
        self.assertNotRegex(content, r"(?i)\bTODO\b|\[TODO")
        self.assertGreaterEqual(len(content.splitlines()), 80)
        self.assertLessEqual(len(content.splitlines()), 120)
        self.assertFalse((self.ROOT / "skills").exists())
        self.assertEqual([self.ROOT / "SKILL.md"], list(self.ROOT.rglob("SKILL.md")))

    def test_root_skill_description_exposes_trigger_and_routing_boundaries(self):
        frontmatter = re.match(r"\A---\n(.*?)\n---\n", self.read("SKILL.md"), re.S).group(1)
        for operation in ("创建", "审阅", "追加", "插入", "回填", "更正", "重组", "归档"):
            self.assertIn(operation, frontmatter)
        for boundary in (
            "普通文档",
            "行式数据",
            "任务分配",
            "会议原始产物",
            "云盘文件管理",
            "服务器运行日志",
        ):
            self.assertIn(boundary, frontmatter)
        self.assertIn("Docx", frontmatter)
        self.assertIn("Wiki", frontmatter)
        self.assertIn("历史", frontmatter)
        self.assertRegex(self.read("SKILL.md"), r"云盘.*`lark-drive`")

    def test_readme_routes_drive_operations_to_lark_drive(self):
        self.assertRegex(self.read("README.md"), r"云盘.*`lark-drive`")

    def test_root_skill_links_every_reference_with_a_conditional_loading_rule(self):
        content = self.read("SKILL.md")
        for reference in self.REQUIRED_REFERENCES:
            matching_lines = [
                line for line in content.splitlines()
                if f"references/{reference}" in line
            ]
            self.assertEqual(1, len(matching_lines), reference)
            self.assertRegex(matching_lines[0], r"当|若|需要")

    def test_public_compatibility_and_agent_metadata_are_exact(self):
        self.assertEqual(
            self.EXPECTED_COMPATIBILITY,
            json.loads(self.read("compatibility.json")),
        )
        self.assertEqual(
            PackageValidationContractTests.AGENT_METADATA,
            self.read("agents/openai.yaml").replace("\r\n", "\n"),
        )

    def test_templates_cover_every_log_type_and_pass_the_offline_linter(self):
        lint = importlib.import_module("scripts.lint_log_draft")
        content = self.read("references/templates.md")
        date_labels = {
            "release": "实际交付日期",
            "engineering": "发生时间",
            "project": "更新时间",
            "incident": "发生时间",
            "decision": "生效时间",
            "experiment": "发生时间",
            "audit": "记录时间",
            "support": "发生时间",
        }
        found = []
        for log_type in self.LOG_TYPES:
            self.assertIn(f"](#{log_type})", content)
            self.assertIn(f'<a id="{log_type}"></a>', content)
            match = re.search(
                rf"(?ms)^## [^\n]*\({log_type}\)\s*$.*?^```markdown\s*$\n(.*?)^```\s*$",
                content,
            )
            self.assertIsNotNone(match, log_type)
            found.append(log_type)
            template = match.group(1)
            for field in ("受众", "敏感级别", "时区"):
                self.assertRegex(template, rf"(?m)^{field}：")
            for distinction in ("事实", "推测", "决定", "待确认"):
                self.assertRegex(template, rf"(?m)^## {distinction}$")
            self.assertRegex(template, rf"(?m)^{date_labels[log_type]}：")
            self.assertEqual([], lint.lint_draft(template, log_type=log_type))
        self.assertEqual(list(self.LOG_TYPES), found)

    def test_taxonomy_defines_exact_log_types_time_semantics_and_support_fields(self):
        content = self.read("references/log-taxonomy.md")
        type_rows = re.findall(r"(?m)^\| `([^`]+)` \|", content)
        self.assertEqual(list(self.LOG_TYPES), type_rows)
        for field in ("occurred_at", "effective_at", "published_at", "recorded_at", "updated_at"):
            self.assertIn(f"`{field}`", content)
        for field in ("问题", "客户影响", "处理或结果", "证据", "状态", "后续"):
            self.assertIn(field, content)
        self.assertIn("提交日期", content)
        self.assertIn("回填", content)
        self.assertIn("预告", content)

    def test_readme_ci_and_ignore_contract_are_publish_safe(self):
        readme = self.read("README.md")
        self.assertRegex(readme.lstrip().splitlines()[0], r"[\u4e00-\u9fff]")
        self.assertRegex(readme, r"(?m)^## English summary$")
        self.assertIn("不授予任何许可证", readme)
        self.assertNotIn("开源项目", readme)
        ignore = set(self.read(".gitignore").splitlines())
        self.assertTrue({"/.local/", "/dist/", "/.superpowers/", "/__pycache__/", "*.pyc"} <= ignore)
        workflow = self.read(".github/workflows/validate.yml")
        for fragment in (
            "permissions:\n  contents: read",
            'python-version: "3.12"',
            "python -m unittest discover -s tests -q",
            "python scripts/validate_package.py --root . --mode source",
        ):
            self.assertIn(fragment, workflow)
        self.assertNotRegex(workflow, r"(?i)npx|login|auth|secret|feishu|lark")

    def test_reference_boundaries_cover_history_privacy_and_document_accessibility(self):
        writing = self.read("references/writing-quality.md")
        for audience in ("客户", "工程团队", "管理层", "审计人员"):
            self.assertIn(audience, writing)
        for distinction in ("事实", "推测", "决定", "待确认"):
            self.assertIn(distinction, writing)
        layout = self.read("references/layout-and-visuals.md")
        for phrase in ("4–6", "折线图", "条形图", "替代文本", "等价正文", "图片元数据"):
            self.assertIn(phrase, layout)
        private = self.read("references/private-memory-contract.md")
        self.assertIn("`{version: 1, profiles: [...]}`", private)
        for path in (".local/index.json", ".local/documents/", ".local/archive/"):
            self.assertIn(path, private)
        for field in (
            "version", "profiles", "profile_id", "aliases", "memory_path",
            "canonical_url", "document_token", "wiki_node_token", "document_kind",
            "log_types", "audience", "language", "timezone", "date_semantics",
            "ordering", "stable_headings", "insertion_rules", "protected_regions",
            "sensitivity", "visual_policy", "correction_policy", "migrated_at",
            "last_verified_at",
        ):
            self.assertIn(f"`{field}`", private)
        for prohibition in ("凭据", "认证状态", "个人信息", "块 ID", "revision", "owner ID", "原始 API 返回"):
            self.assertIn(prohibition, private)
        execution = self.read("references/execution-and-safety.md")
        for phrase in ("1.0.72", "能力检测", "乐观并发", "逻辑锚点", "结果未知", "差异预览", "ok=true", "warning"):
            self.assertIn(phrase, execution)


if __name__ == "__main__":
    unittest.main()
