"""Offline public-package validation for the feishu-cloud-logs skill."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


REQUIRED_SCRIPTS = ("resolve_runtime.py", "private_memory.py", "lint_log_draft.py", "validate_package.py")
REQUIRED_REFERENCES = ("log-taxonomy.md", "templates.md", "writing-quality.md", "layout-and-visuals.md", "private-memory-contract.md", "execution-and-safety.md")
REQUIRED_TESTS = ("test_runtime.py", "test_private_memory.py", "test_draft_lint.py", "test_package.py")
EXPECTED_AGENT_METADATA = (
    "interface:\n"
    '  display_name: "飞书云日志"\n'
    '  short_description: "将技术与业务变化整理为专业、可验证的飞书云文档日志"\n'
    '  default_prompt: "Use $feishu-cloud-logs to draft or safely maintain a professional Feishu cloud-document log."\n'
    "policy:\n"
    "  allow_implicit_invocation: true\n"
)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


@dataclass(frozen=True)
class ValidationError:
    code: str
    message: str


@dataclass(frozen=True)
class PackageReport:
    errors: list[ValidationError]


def _files_for_scan(root: Path, tracked_paths: Iterable[str] | None) -> list[Path]:
    if tracked_paths is not None:
        return [root / Path(item) for item in tracked_paths if not Path(item).is_absolute() and ".." not in Path(item).parts]
    files: list[Path] = []
    for directory, names, filenames in os.walk(root):
        names[:] = [name for name in names if name not in {".git", ".local", ".superpowers", "dist", "__pycache__"}]
        files.extend(Path(directory) / filename for filename in filenames)
    return files


def _pinned_for_package(spec: object, package: str) -> bool:
    return isinstance(spec, str) and bool(re.fullmatch(re.escape(package) + r"@\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.-]+)?", spec))


def _valid_compatibility(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict) or type(data.get("version")) is not int or data.get("version") != 1 or data.get("policy") != "capability-gated":
        return False
    package = data.get("package")
    tested = data.get("tested_versions")
    skills = data.get("required_skills")
    capabilities = data.get("required_capabilities")
    if not (
        isinstance(package, str)
        and re.fullmatch(r"@[a-z0-9-]+/[a-z0-9-]+", package, re.I)
        and _pinned_for_package(data.get("pinned_fallback"), package)
        and all(
            isinstance(value, list) and value
            and all(isinstance(item, str) and item for item in value)
            for value in (tested, skills, capabilities)
        )
        and all(re.fullmatch(r"\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.-]+)?", item) for item in tested)
    ):
        return False
    if "profiles" not in data:
        return True
    profiles = data["profiles"]
    if not isinstance(profiles, dict) or not profiles:
        return False
    for name, profile in profiles.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(profile, dict):
            return False
        for field, complete_set in (("required_skills", skills), ("required_capabilities", capabilities)):
            selected = profile.get(field)
            if not isinstance(selected, list) or not all(isinstance(item, str) and item for item in selected):
                return False
            if not set(selected) <= set(complete_set):
                return False
    return True


def _is_allowed_path(relative: Path) -> bool:
    parts = relative.parts
    if relative in {Path(".gitignore"), Path("SKILL.md"), Path("README.md"), Path("compatibility.json"), Path("agents/openai.yaml")}:
        return True
    if len(parts) == 2 and parts[0] == "scripts" and parts[1] in REQUIRED_SCRIPTS:
        return True
    if len(parts) == 2 and parts[0] == "references" and parts[1] in REQUIRED_REFERENCES:
        return True
    if len(parts) == 2 and parts[0] == "tests" and parts[1] in REQUIRED_TESTS:
        return True
    return relative == Path(".github/workflows/validate.yml")


def _normalized_manifest(tracked_paths: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for item in tracked_paths:
        if not isinstance(item, str):
            continue
        path = Path(item)
        if path.is_absolute() or ".." in path.parts:
            continue
        normalized.append(path.as_posix())
    return tuple(normalized)


def validate_package(root: Path, tracked_paths: Iterable[str] | None = None, *, mode: str = "package", allow_local: bool = False) -> PackageReport:
    root = Path(root)
    errors: list[ValidationError] = []
    seen: set[str] = set()
    def add(code: str, message: str) -> None:
        if code not in seen:
            errors.append(ValidationError(code, message))
            seen.add(code)
    def read_public(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            add("invalid_public_encoding", "Public files must be UTF-8 text.")
            return None
    if mode not in {"package", "source", "installed"}:
        add("invalid_mode", "Validation mode must be package, source, or installed.")
        return PackageReport(errors)
    if mode == "source" and tracked_paths is None:
        tracked_paths = _tracked_paths(root)
        if tracked_paths is None:
            add(
                "tracked_manifest_unavailable",
                "Source validation requires an available tracked-file manifest.",
            )
            tracked_paths = ()
    manifest = _normalized_manifest(tracked_paths or ())
    public_files = _files_for_scan(root, manifest if mode == "source" else None)

    required = (".gitignore", "SKILL.md", "README.md", "compatibility.json", "agents/openai.yaml", ".github/workflows/validate.yml", *(f"scripts/{name}" for name in REQUIRED_SCRIPTS), *(f"references/{name}" for name in REQUIRED_REFERENCES), *(f"tests/{name}" for name in REQUIRED_TESTS))
    if mode == "source":
        missing_tracked = sorted(set(required) - set(manifest))
        if missing_tracked:
            add(
                "missing_tracked_required_file",
                "Required public files must be present in the tracked manifest: "
                + ", ".join(missing_tracked),
            )
    for name in required:
        if not (root / name).is_file():
            add("missing_root_skill" if name == "SKILL.md" else "missing_required_file", "Missing required file: " + name)
    if (root / "LICENSE").exists():
        add("license_not_allowed", "LICENSE is not part of this package.")
    for skill in (path for path in public_files if path.name == "SKILL.md"):
        if skill != root / "SKILL.md" and ".git" not in skill.parts and ".local" not in skill.parts:
            add("nested_skill_layout", "Only the root SKILL.md is allowed.")
    approved_top_level = {".gitignore", "SKILL.md", "README.md", "compatibility.json", "agents", "scripts", "references", "tests", ".github"}
    for child in root.iterdir():
        if child.name not in approved_top_level and child.name not in {".git", ".local", ".superpowers", "dist", "__pycache__"}:
            add("unapproved_top_level", "Unapproved public top-level entry: " + child.name)
    for path in public_files:
        relative = path.relative_to(root)
        if ".local" not in relative.parts and not _is_allowed_path(relative):
            if relative.parts and relative.parts[0] == "scripts":
                add("unapproved_script", "Unapproved script path: " + relative.as_posix())
            elif relative.parts and relative.parts[0] == "references":
                add("unapproved_reference", "Unapproved reference path: " + relative.as_posix())
            else:
                add("unapproved_public_file", "Unapproved public file: " + relative.as_posix())
    skill = root / "SKILL.md"
    if skill.is_file():
        content = read_public(skill)
        if content is None:
            content = ""
        if len(content) > 20000:
            add("skill_body_too_long", "SKILL.md must remain concise.")
        for reference in REQUIRED_REFERENCES:
            if f"references/{reference}" not in content:
                add("missing_reference_link", "SKILL.md must link " + reference)
    readme = root / "README.md"
    if readme.is_file():
        content = read_public(readme)
        if content is None:
            content = ""
        content = content.lstrip()
        if not re.search(r"[\u4e00-\u9fff]", content.split("\n", 1)[0]):
            add("readme_not_chinese_first", "README must begin in Chinese.")
        if not re.search(r"(?im)^##\s+English summary\s*$", content):
            add("readme_missing_english_summary", "README needs an English summary.")
    agent = root / "agents/openai.yaml"
    if agent.is_file():
        content = read_public(agent)
        if content is None:
            content = ""
        normalized_agent = content.replace("\r\n", "\n").replace("\r", "\n")
        if not re.search(r'(?m)^  display_name: "飞书云日志"$', normalized_agent):
            add("metadata_mismatch", "Agent metadata must match the root skill.")
        if normalized_agent != EXPECTED_AGENT_METADATA:
            add("invalid_agent_schema", "Agent metadata must match the exact public schema.")
    compatibility = root / "compatibility.json"
    if compatibility.is_file() and not _valid_compatibility(compatibility):
        add("invalid_compatibility_schema", "compatibility.json must declare the capability-gated runtime contract.")
    if mode == "package" and not allow_local and (root / ".local").exists():
        add("local_content_in_package", "Ignored .local content must not be packaged.")
    ignore = root / ".gitignore"
    if ignore.is_file():
        try:
            patterns = set(ignore.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeError):
            add("invalid_gitignore", ".gitignore must be UTF-8 and readable.")
            patterns = set()
        if not {"/.local/", "/dist/", "/__pycache__/", "*.pyc", "/.superpowers/"} <= patterns:
            add("invalid_gitignore", ".gitignore must contain the five approved exclusion rules.")
    for item in manifest:
        relative = Path(item)
        if relative.parts and relative.parts[0] == ".local":
            add("tracked_private_path", "Tracked paths must never include .local.")
    scan_files = _files_for_scan(root, manifest if mode == "source" else None)
    for path in scan_files:
        if not path.is_file() or ".local" in path.parts:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            add("invalid_public_encoding", "Public files must be UTF-8 text.")
            continue
        privacy_pattern = r"private[-_ ]project|\b(?:go" + r"shop|yuko" + r"cart)\b"
        if re.search(privacy_pattern, content, re.I):
            add("private_" + "project_term", "Public package contains a private " + "project term.")
        target_url_pattern = r"http" + r"s?://[^\s]*(?:fe" + r"ishu\.cn|larksuite\.com)/(?:docx|wiki)/[^\s]+"
        target_field_pattern = (
            r"\b(?:document[_ -]?token|wiki[_ -]?node[_ -]?token|canonical[_ -]?url|target[_ -]?token)"
            r"\b[\"']?\s*[:=]\s*(?:[\"'][A-Za-z0-9_-]{8,128}[\"']|(?=[A-Za-z0-9_-]{8,128}\b)(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{8,128}|[A-Za-z]{8,128}\b)"
        )
        if re.search(target_url_pattern + r"|\bdoxcn[A-Za-z0-9]{8,}\b|\bou_[A-Za-z0-9]{10,}\b|" + target_field_pattern, content, re.I):
            add("feishu_identifier", "Public package contains a real-looking Feishu identifier.")
        legacy_pattern = r"\bmcp" + r"__[A-Za-z0-9_]*feishu|\b(?:get_" + r"feishu_document_[A-Za-z0-9_]*|create_" + r"feishu_document)\b"
        if re.search(legacy_pattern, content, re.I):
            add("legacy_mcp_name", "Public package contains a legacy MCP name.")
        unpinned_content = content
        if path == root / "compatibility.json":
            try:
                structured = json.loads(content)
                if isinstance(structured, dict):
                    structured["package"] = ""
                    structured["pinned_fallback"] = ""
                    unpinned_content = json.dumps(structured, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                pass
        if re.search(r"@(?:larksuite/cli|lark-cli)(?:@(?!\d)(?:latest|next|\*))?(?!@\d)", unpinned_content, re.I):
            add("unpinned_cli", "Public package contains an unversioned or mutable CLI invocation.")
    return PackageReport(errors)


def _tracked_paths(root: Path) -> list[str] | None:
    result = subprocess.run(["git", "ls-files"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, shell=False)
    return [line for line in result.stdout.splitlines() if line] if result.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(description="Validate a public Feishu cloud-logs package without network access.")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--mode", choices=("package", "source", "installed"), default="package")
    parser.add_argument("--allow-local", action="store_true")
    try:
        args = parser.parse_args(argv)
        tracked = _tracked_paths(args.root) if args.mode == "source" else None
        report = validate_package(args.root, tracked_paths=tracked, mode=args.mode, allow_local=args.allow_local)
    except (ValueError, OSError, UnicodeError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": not report.errors, "errors": [asdict(error) for error in report.errors]}, ensure_ascii=False, sort_keys=True))
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
