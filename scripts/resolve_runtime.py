"""Resolve and safely probe a capability-compatible local CLI."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


class RuntimeResolutionError(ValueError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise RuntimeResolutionError(message)


@dataclass(frozen=True)
class RuntimeResolution:
    package: str
    version: str
    capabilities: set[str]
    tested: bool


def _pinned_spec(spec: str) -> tuple[str, str]:
    package, marker, version = spec.rpartition("@")
    if not marker or not package or not re.fullmatch(r"\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.-]+)?", version):
        raise RuntimeResolutionError("runtime package specification must be pinned")
    return package, version


def _resolution(candidate: object, package: str, required: set[str], tested: set[str], *, required_version: str | None = None) -> RuntimeResolution:
    if not isinstance(candidate, Mapping):
        raise RuntimeResolutionError("runtime candidate must be an object")
    version = candidate.get("version")
    if candidate.get("package") != package or not isinstance(version, str) or (required_version is not None and version != required_version):
        raise RuntimeResolutionError("runtime does not match the requested package")
    raw_capabilities = candidate.get("capabilities", ())
    if not isinstance(raw_capabilities, (list, tuple, set)) or not all(isinstance(item, str) for item in raw_capabilities):
        raise RuntimeResolutionError("runtime capabilities must be a string array")
    capabilities = set(raw_capabilities)
    missing = required - capabilities
    if missing:
        raise RuntimeResolutionError("missing required capabilities: " + ", ".join(sorted(missing)))
    return RuntimeResolution(package=package, version=version, capabilities=capabilities, tested=version in tested)


def resolve_runtime(installed: Iterable[Mapping[str, object]], required_capabilities: Iterable[str], tested_versions: Iterable[str], requested_spec: str, *, allow_fallback_download: bool = False, download_fallback: Callable[[str], Mapping[str, object]] | None = None) -> RuntimeResolution:
    """Use any compatible installed version; fallback is explicit and pinned."""
    package, fallback_version = _pinned_spec(requested_spec)
    required, tested = set(required_capabilities), set(tested_versions)
    if not isinstance(installed, Iterable) or isinstance(installed, (str, bytes, dict)):
        raise RuntimeResolutionError("installed runtimes must be a JSON array")
    candidate_error: RuntimeResolutionError | None = None
    for candidate in installed:
        if not isinstance(candidate, Mapping):
            raise RuntimeResolutionError("installed runtime items must be objects")
        capabilities = candidate.get("capabilities", ())
        if not isinstance(capabilities, (list, tuple, set)) or not all(isinstance(item, str) for item in capabilities):
            raise RuntimeResolutionError("installed runtime capabilities must be string arrays")
        if candidate.get("package") != package:
            continue
        try:
            return _resolution(candidate, package, required, tested)
        except RuntimeResolutionError as error:
            candidate_error = error
    if not allow_fallback_download:
        if candidate_error is not None:
            raise candidate_error
        raise RuntimeResolutionError("no installed runtime satisfies the required capabilities")
    if download_fallback is None:
        raise RuntimeResolutionError("fallback download was enabled but no fallback provider was supplied")
    return _resolution(download_fallback(requested_spec), package, required, tested, required_version=fallback_version)


def probe_runtime(executable: str | Sequence[str], capability_commands: Mapping[str, tuple[str, ...]], *, package: str | None = None, expected_markers: Mapping[str, str] | None = None, runner: Callable[..., object] = subprocess.run, allow_fallback_download: bool = False, executable_resolver: Callable[[str], str | None] = shutil.which) -> dict[str, object]:
    """Use only argv-array --version/--help probes; no auth, install, or document access."""
    prefix = [executable] if isinstance(executable, str) else list(executable)
    if not prefix or not all(isinstance(part, str) and part for part in prefix):
        raise RuntimeResolutionError("runtime probe requires a non-empty argv prefix")
    download_launchers = {"npx", "npx.cmd", "npm", "npm.cmd", "pnpm", "yarn"}
    download_capable = Path(prefix[0]).name.lower() in download_launchers
    if not download_capable and len(prefix) != 1:
        raise RuntimeResolutionError("non-download runtime probes require a single executable")
    if download_capable:
        if not allow_fallback_download:
            raise RuntimeResolutionError("download-capable runtime prefixes require explicit fallback permission")
        pinned_specs = [
            part
            for part in prefix
            if re.fullmatch(r"@[A-Za-z0-9-]+/[A-Za-z0-9-]+@\d+(?:\.\d+)*(?:[-+][A-Za-z0-9.-]+)?", part)
        ]
        if len(pinned_specs) != 1:
            raise RuntimeResolutionError("download-capable runtime prefix must contain an exact pinned package")
        safe_download_flags = {"--yes", "-y"}
        if prefix[-1] != pinned_specs[0] or any(part not in safe_download_flags for part in prefix[1:-1]):
            raise RuntimeResolutionError(
                "download-capable runtime prefix may contain only safe flags followed by the pinned package"
            )
        if not isinstance(package, str) or not package.strip() or Path(package).name.lower() in download_launchers:
            raise RuntimeResolutionError("download-capable runtime probes require an explicit logical package")
        pinned_package, _ = _pinned_spec(pinned_specs[0])
        if pinned_package != package:
            raise RuntimeResolutionError("pinned runtime prefix package must match the logical package")
    elif package is not None and (not isinstance(package, str) or not package.strip()):
        raise RuntimeResolutionError("runtime logical package must be a non-empty string")
    resolved = executable_resolver(prefix[0])
    if not resolved:
        raise RuntimeResolutionError("runtime executable is not locally available")
    prefix[0] = resolved
    logical_package = package or Path(prefix[0]).stem
    if not logical_package:
        raise RuntimeResolutionError("runtime probe requires an explicit logical package")

    def run(suffix: list[str]) -> object:
        argv = [*prefix, *suffix]
        result = runner(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, shell=False)
        if getattr(result, "returncode", 1) != 0:
            raise RuntimeResolutionError("safe runtime probe failed: " + " ".join(argv))
        return result

    version = re.search(r"\d+(?:\.\d+)+", str(getattr(run(["--version"]), "stdout", "")))
    if not version:
        raise RuntimeResolutionError("runtime version probe did not return a version")
    capabilities: set[str] = set()
    for capability, command in capability_commands.items():
        if not command or any(part.lower() in {"auth", "login", "install", "delete"} for part in command):
            raise RuntimeResolutionError("unsafe capability probe requested")
        result = run([*command, "--help"])
        marker = (expected_markers or {}).get(capability)
        if marker and marker not in (str(getattr(result, "stdout", "")) + str(getattr(result, "stderr", ""))):
            raise RuntimeResolutionError("capability probe did not expose expected marker: " + capability)
        capabilities.add(capability)
    return {"package": logical_package, "version": version.group(0), "capabilities": capabilities}


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(description="Validate a caller-supplied runtime inventory without probing or network access.")
    parser.add_argument("--installed-json", required=True)
    parser.add_argument("--required-capability", action="append", default=[])
    parser.add_argument("--tested-version", action="append", default=[])
    parser.add_argument("--requested-spec", required=True)
    try:
        args = parser.parse_args(argv)
        resolution = resolve_runtime(json.loads(args.installed_json), args.required_capability, args.tested_version, args.requested_spec)
    except (RuntimeResolutionError, json.JSONDecodeError, TypeError, UnicodeError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    data = asdict(resolution)
    data["capabilities"] = sorted(data["capabilities"])
    print(json.dumps({"ok": True, "source": "supplied_inventory", "runtime": data}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
