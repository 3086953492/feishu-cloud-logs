import importlib
import json
import unittest
from pathlib import Path


PACKAGE = "@larksuite/" + "cli"


class RuntimeResolverContractTests(unittest.TestCase):
    def runtime(self):
        return importlib.import_module("scripts.resolve_runtime")

    def compatibility(self):
        path = Path(__file__).resolve().parents[1] / "compatibility.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_draft_profile_declares_no_runtime_or_skill_requirements(self):
        profile = self.compatibility()["profiles"]["draft"]

        self.assertEqual([], profile["required_skills"])
        self.assertEqual([], profile["required_capabilities"])

    def test_docx_profiles_resolve_without_unrelated_runtime_capabilities(self):
        runtime = self.runtime()
        compatibility = self.compatibility()
        available_by_operation = {
            "docx_read": {"docs.fetch.v2"},
            "docx_create": {"docs.create.v2", "docs.fetch.v2"},
            "docx_update": {"docs.fetch.v2", "docs.update.v2"},
        }
        for operation, available in available_by_operation.items():
            with self.subTest(operation=operation):
                profile = compatibility["profiles"][operation]
                resolution = runtime.resolve_runtime(
                    [{"package": PACKAGE, "version": "2.4.0", "capabilities": available}],
                    profile["required_capabilities"],
                    compatibility["tested_versions"],
                    compatibility["pinned_fallback"],
                )

                self.assertEqual(available, resolution.capabilities)
                self.assertEqual(["lark-doc"], profile["required_skills"])

    def test_wiki_and_risk_capabilities_are_required_only_for_selected_operations(self):
        runtime = self.runtime()
        compatibility = self.compatibility()
        profiles = compatibility["profiles"]
        docx_capabilities = {"docs.fetch.v2", "docs.update.v2"}
        selected_operations = (
            (set(profiles["docx_update"]["required_capabilities"])
             | set(profiles["wiki_resolve"]["required_capabilities"]), "wiki.resolve"),
            (set(profiles["destructive_update"]["required_capabilities"]), "risk.confirmation"),
        )
        for required, missing in selected_operations:
            with self.subTest(missing=missing):
                def resolve(available):
                    return runtime.resolve_runtime(
                        [{"package": PACKAGE, "version": "2.4.0", "capabilities": available}],
                        required, compatibility["tested_versions"], compatibility["pinned_fallback"],
                    )

                with self.assertRaisesRegex(runtime.RuntimeResolutionError, missing):
                    resolve(docx_capabilities)
                self.assertEqual(docx_capabilities | {missing}, resolve(docx_capabilities | {missing}).capabilities)

    def test_probe_executes_only_the_selected_docx_profile_help_commands(self):
        runtime = self.runtime()
        profile = self.compatibility()["profiles"]["docx_update"]
        commands = {
            "skills.read": ("skills", "read"),
            "docs.create.v2": ("docs", "+create"),
            "docs.fetch.v2": ("docs", "+fetch"),
            "docs.update.v2": ("docs", "+update"),
            "wiki.resolve": ("wiki", "nodes", "get"),
            "risk.confirmation": ("docs", "+update"),
        }
        calls = []
        outputs = {
            ("C:/tools/lark.exe", "--version"): "2.4.0",
            ("C:/tools/lark.exe", "docs", "+fetch", "--help"): "fetch help",
            ("C:/tools/lark.exe", "docs", "+update", "--help"): "update help",
        }

        def runner(argv, **kwargs):
            calls.append(argv)
            self.assertFalse(kwargs["shell"])
            return type("Result", (), {"returncode": 0, "stdout": outputs[tuple(argv)], "stderr": ""})()

        probe = runtime.probe_runtime(
            "lark", {capability: commands[capability] for capability in profile["required_capabilities"]},
            package=PACKAGE, runner=runner,
            expected_markers={"risk.confirmation": "--confirm-risk"},
            executable_resolver=lambda name: "C:/tools/lark.exe",
        )

        self.assertEqual([list(argv) for argv in outputs], calls)
        self.assertEqual({"docs.fetch.v2", "docs.update.v2"}, probe["capabilities"])

    def test_resolves_a_capability_compatible_cli_and_marks_untested_version(self):
        runtime = self.runtime()

        resolution = runtime.resolve_runtime(
            installed=[
                {
                    "package": PACKAGE,
                    "version": "2.4.0",
                    "capabilities": {"docs.read", "docs.update"},
                }
            ],
            required_capabilities={"docs.read", "docs.update"},
            tested_versions={"2.3.1"},
            requested_spec="@larksuite/cli@2.4.0",
        )

        self.assertEqual("2.4.0", resolution.version)
        self.assertFalse(resolution.tested)
        self.assertEqual({"docs.read", "docs.update"}, resolution.capabilities)

    def test_rejects_installed_cli_without_a_required_capability(self):
        runtime = self.runtime()

        with self.assertRaises(runtime.RuntimeResolutionError) as raised:
            runtime.resolve_runtime(
                installed=[
                    {
                        "package": PACKAGE,
                        "version": "2.3.1",
                        "capabilities": {"docs.read"},
                    }
                ],
                required_capabilities={"docs.read", "docs.update"},
                tested_versions={"2.3.1"},
                requested_spec="@larksuite/cli@2.3.1",
            )

        self.assertIn("docs.update", str(raised.exception))

    def test_rejects_an_unpinned_latest_specifier_before_resolving(self):
        runtime = self.runtime()

        with self.assertRaises(runtime.RuntimeResolutionError):
            runtime.resolve_runtime(
                installed=[],
                required_capabilities={"docs.read"},
                tested_versions=set(),
                requested_spec="@larksuite/" + "cli@" + "latest",
            )

    def test_never_downloads_a_fallback_without_an_explicit_allow_flag(self):
        runtime = self.runtime()
        downloads = []

        def download(spec):
            downloads.append(spec)
            return {
                "package": PACKAGE,
                "version": "2.3.1",
                "capabilities": {"docs.read"},
            }

        with self.assertRaises(runtime.RuntimeResolutionError):
            runtime.resolve_runtime(
                installed=[],
                required_capabilities={"docs.read"},
                tested_versions={"2.3.1"},
                requested_spec="@larksuite/cli@2.3.1",
                download_fallback=download,
            )

        self.assertEqual([], downloads)

    def test_allows_an_explicitly_requested_pinned_fallback_download(self):
        runtime = self.runtime()

        resolution = runtime.resolve_runtime(
            installed=[],
            required_capabilities={"docs.read"},
            tested_versions={"2.3.1"},
            requested_spec="@larksuite/cli@2.3.1",
            allow_fallback_download=True,
            download_fallback=lambda spec: {
                "package": PACKAGE,
                "version": "2.3.1",
                "capabilities": {"docs.read"},
            },
        )

        self.assertTrue(resolution.tested)

    def test_rejects_an_allowed_fallback_that_lacks_a_required_capability(self):
        runtime = self.runtime()

        with self.assertRaises(runtime.RuntimeResolutionError) as raised:
            runtime.resolve_runtime(
                installed=[],
                required_capabilities={"docs.read", "docs.update"},
                tested_versions={"2.3.1"},
                requested_spec="@larksuite/cli@2.3.1",
                allow_fallback_download=True,
                download_fallback=lambda spec: {
                    "package": PACKAGE,
                    "version": "2.3.1",
                    "capabilities": {"docs.read"},
                },
            )

        self.assertIn("docs.update", str(raised.exception))

    def test_probe_uses_argv_arrays_and_only_safe_capability_commands(self):
        runtime = self.runtime()
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            outputs = {
                ("C:/tools/lark.exe", "--version"): "2.3.1\\n",
                ("C:/tools/lark.exe", "skills", "read", "--help"): "read a skill\\n",
                ("C:/tools/lark.exe", "docs", "update", "--help"): "update a document\\n",
            }
            return type("Result", (), {"returncode": 0, "stdout": outputs[tuple(argv)], "stderr": ""})()

        probe = runtime.probe_runtime(
            executable="lark",
            package=PACKAGE,
            capability_commands={
                "skills.read": ("skills", "read"),
                "docs.update": ("docs", "update"),
            },
            runner=runner,
            executable_resolver=lambda name: "C:/tools/lark.exe",
        )

        self.assertEqual("2.3.1", probe["version"])
        self.assertEqual({"skills.read", "docs.update"}, probe["capabilities"])
        self.assertEqual(
            [["C:/tools/lark.exe", "--version"], ["C:/tools/lark.exe", "skills", "read", "--help"], ["C:/tools/lark.exe", "docs", "update", "--help"]],
            [call[0] for call in calls],
        )
        self.assertTrue(all(call[1]["shell"] is False for call in calls))

    def test_accepts_a_capability_compatible_installed_version_that_differs_from_pinned_fallback(self):
        runtime = self.runtime()

        resolution = runtime.resolve_runtime(
            installed=[{"package": PACKAGE, "version": "2.4.0", "capabilities": {"docs.read"}}],
            required_capabilities={"docs.read"},
            tested_versions={"2.3.1"},
            requested_spec="@larksuite/cli@2.3.1",
        )

        self.assertEqual("2.4.0", resolution.version)
        self.assertFalse(resolution.tested)

    def test_probe_accepts_an_argv_prefix_without_shell_composition(self):
        runtime = self.runtime()
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return type("Result", (), {"returncode": 0, "stdout": "2.3.1", "stderr": ""})()

        runtime.probe_runtime(
            executable=["npx", "--yes", "@larksuite/cli@2.3.1"],
            package=PACKAGE,
            capability_commands={},
            runner=runner,
            allow_fallback_download=True,
            executable_resolver=lambda name: "C:/tools/npx.cmd",
        )

        self.assertEqual(["C:/tools/npx.cmd", "--yes", "@larksuite/cli@2.3.1", "--version"], calls[0])

    def test_probe_rejects_commands_around_a_downloaded_package_before_running(self):
        runtime = self.runtime()
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return type("Result", (), {"returncode": 0, "stdout": "1.0.72", "stderr": ""})()

        pinned = PACKAGE + "@1.0.72"
        prefixes = (
            ["npx.cmd", "--yes", pinned, "auth", "login"],
            ["npx.cmd", "--yes", "auth", "login", pinned],
        )
        for prefix in prefixes:
            with self.subTest(prefix=prefix), self.assertRaises(runtime.RuntimeResolutionError):
                runtime.probe_runtime(
                    prefix,
                    {},
                    package=PACKAGE,
                    runner=runner,
                    allow_fallback_download=True,
                    executable_resolver=lambda name: "C:/tools/npx.cmd",
                )

        self.assertEqual([], calls)

    def test_cli_usage_errors_are_json(self):
        runtime = self.runtime()
        from contextlib import redirect_stdout
        from io import StringIO

        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(2, runtime.main([]))
        self.assertFalse(__import__("json").loads(output.getvalue())["ok"])

    def test_probe_rejects_download_capable_prefix_without_explicit_fallback_flag(self):
        runtime = self.runtime()
        with self.assertRaises(runtime.RuntimeResolutionError):
            runtime.probe_runtime(["npx", "--yes", "@larksuite/cli@2.3.1"], {}, executable_resolver=lambda name: name)

    def test_download_capable_probe_requires_a_non_launcher_logical_package(self):
        runtime = self.runtime()

        def runner(argv, **kwargs):
            return type("Result", (), {"returncode": 0, "stdout": "2.3.1", "stderr": ""})()

        for package in (None, "npx"):
            with self.subTest(package=package), self.assertRaises(runtime.RuntimeResolutionError):
                runtime.probe_runtime(
                    ["npx", "--yes", "@larksuite/cli@2.3.1"],
                    {},
                    package=package,
                    runner=runner,
                    allow_fallback_download=True,
                    executable_resolver=lambda name: "C:/tools/runtime-wrapper.exe",
                )

    def test_probe_requires_a_local_resolved_executable(self):
        runtime = self.runtime()
        with self.assertRaises(runtime.RuntimeResolutionError):
            runtime.probe_runtime("missing-lark", {}, executable_resolver=lambda name: None)

    def test_malformed_installed_json_and_items_return_json_exit_2(self):
        runtime = self.runtime()
        from contextlib import redirect_stdout
        from io import StringIO
        for installed in ("not-json", "[null]", '[{"package": "@larksuite/' + 'cli", "capabilities": "docs.read"}]'):
            with self.subTest(installed=installed):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(2, runtime.main(["--installed-json", installed, "--requested-spec", "@larksuite/cli@2.3.1"]))
                self.assertFalse(__import__("json").loads(output.getvalue())["ok"])

    def test_rejects_malformed_fallback_capabilities(self):
        runtime = self.runtime()
        with self.assertRaises(runtime.RuntimeResolutionError):
            runtime.resolve_runtime(
                [], {"docs.read"}, {"2.3.1"}, PACKAGE + "@2.3.1",
                allow_fallback_download=True,
                download_fallback=lambda spec: {"package": PACKAGE, "version": "2.3.1", "capabilities": "docs.read"},
            )

    def test_malformed_fallback_candidates_always_raise_runtime_resolution_error(self):
        runtime = self.runtime()
        candidates = (
            None,
            "not an object",
            {"package": PACKAGE, "version": "2.3.1", "capabilities": None},
            {"package": PACKAGE, "version": None, "capabilities": ["docs.read"]},
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises(runtime.RuntimeResolutionError):
                runtime.resolve_runtime(
                    [],
                    {"docs.read"},
                    {"2.3.1"},
                    PACKAGE + "@2.3.1",
                    allow_fallback_download=True,
                    download_fallback=lambda spec, value=candidate: value,
                )

    def test_npx_pinned_package_must_match_the_explicit_logical_package(self):
        runtime = self.runtime()
        with self.assertRaises(runtime.RuntimeResolutionError):
            runtime.probe_runtime(
                ["npx", "--yes", "@example/runtime@2.3.1"],
                {},
                package=PACKAGE,
                allow_fallback_download=True,
                executable_resolver=lambda name: "C:/tools/npx.cmd",
            )

    def test_non_download_launcher_cannot_wrap_an_arbitrary_argv_prefix(self):
        runtime = self.runtime()
        prefixes = (
            ["cmd", "/c", "npx", "--yes", PACKAGE + "@2.3.1"],
            ["powershell", "-Command", "npx", "--yes", PACKAGE + "@2.3.1"],
            ["python", "-m", "some_runtime"],
        )
        for prefix in prefixes:
            with self.subTest(prefix=prefix), self.assertRaises(runtime.RuntimeResolutionError):
                runtime.probe_runtime(
                    prefix,
                    {},
                    package=PACKAGE,
                    allow_fallback_download=True,
                    executable_resolver=lambda name: "C:/tools/wrapper.exe",
                )

    def test_capability_marker_is_required_and_probe_flows_into_resolution(self):
        runtime = self.runtime()
        outputs = {
            ("--version",): "2.4.0",
            ("docs", "update", "--help"): "--confirm-risk required for destructive operations",
        }

        def runner(argv, **kwargs):
            suffix = tuple(argv[1:])
            return type("Result", (), {"returncode": 0, "stdout": outputs[suffix], "stderr": ""})()

        probe = runtime.probe_runtime(
            "lark",
            {"risk.confirmation": ("docs", "update")},
            package=PACKAGE,
            expected_markers={"risk.confirmation": "--confirm-risk"},
            runner=runner,
            executable_resolver=lambda name: "C:/tools/lark.exe",
        )
        resolution = runtime.resolve_runtime([probe], {"risk.confirmation"}, {"2.3.1"}, PACKAGE + "@2.3.1")
        self.assertEqual(PACKAGE, probe["package"])
        self.assertEqual("2.4.0", resolution.version)
        self.assertFalse(resolution.tested)

        with self.assertRaises(runtime.RuntimeResolutionError):
            runtime.probe_runtime(
                "lark", {"risk.confirmation": ("docs", "update")}, package=PACKAGE,
                expected_markers={"risk.confirmation": "--missing-marker"}, runner=runner,
                executable_resolver=lambda name: "C:/tools/lark.exe",
            )


if __name__ == "__main__":
    unittest.main()
