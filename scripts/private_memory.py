"""Validate and resolve ignored local document profiles without network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class PrivateMemoryError(ValueError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise PrivateMemoryError(message)


@dataclass(frozen=True)
class MemoryMatch:
    may_mutate: bool
    match_kind: str
    memory_path: Path | None
    trust_status: str


SUPPORTED_LOG_TYPES = {"release", "engineering", "project", "incident", "decision", "experiment", "audit", "support"}
_ALLOWED_KEYS = {
    "profile_id", "aliases", "canonical_url", "document_token", "wiki_node_token", "memory_path", "document_kind", "log_types", "audience", "language", "timezone", "date_semantics", "ordering", "stable_headings", "insertion_rules", "protected_regions", "sensitivity", "visual_policy", "correction_policy", "last_verified_at", "migrated_at",
}
_REQUIRED_KEYS = _ALLOWED_KEYS - {"wiki_node_token"}
_LIST_FIELDS = {"aliases", "log_types", "stable_headings", "protected_regions"}
_STRING_FIELDS = _ALLOWED_KEYS - _LIST_FIELDS - {"last_verified_at"}
_TOKEN = re.compile(r"[A-Za-z0-9_-]{8,128}")
_PROFILE_ID = re.compile(r"(?=.{1,64}\Z)[a-z0-9]+(?:[._-][a-z0-9]+)*")
_LANGUAGE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_TARGET_LIKE = re.compile(r"[A-Za-z0-9_-]{20,128}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENSITIVE = re.compile(r"(?:\b(?:api[_ -]?key|secret|credential|authorization|bearer|password|cookie|session|auth)\b|\b(?:block|revision|owner)[_-]?id\b|\b(?:blk|rev|ou)_[A-Za-z0-9_-]+\b|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(?<![\w-])(?:\+?\d{3}[- ]?\d{3,4}[- ]?\d{4})(?![\w-]))", re.I)
_ACTIVE_UNSAFE = re.compile(r"(?:mcp" + r"__|\b(?:get_|create_)feishu_document|\b(?:block|revision|owner)[_-]?id\b|\braw\s+api\s+(?:response|output)\b|\b(?:authorization|bearer|cookie|session|password|auth)\b|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(?<![\w-])(?:\+?\d{3}[- ]?\d{3,4}[- ]?\d{4})(?![\w-]))", re.I)
_ACTIVE_LEGACY_CLI = re.compile(r"(?:@larksuite/" + r"cli\b|\blark" + r"-cli\b)", re.I)
_ACTIVE_TARGET_URL = re.compile(
    r"https?://(?:[A-Za-z0-9-]+\.)*(?:feishu\.cn|larksuite\.com)/(?:docx|wiki)/[A-Za-z0-9_-]{8,128}",
    re.I,
)
_ACTIVE_TARGET_FIELD = re.compile(
    r"\b(?:canonical[\s_-]*url|document[\s_-]*token|wiki[\s_-]*node[\s_-]*token|target[\s_-]*token)\b",
    re.I,
)
_ACTIVE_CREDENTIAL_VALUE = re.compile(
    r"(?:\b(?:api[_ -]?key|secret|credential)\b\s*[:=]\s*['\"]?[A-Za-z0-9][A-Za-z0-9._/-]{7,}|(?<![\w-])(?:sk|pk)[-_][A-Za-z0-9_-]{12,}(?![\w-]))",
    re.I,
)
_ENUM_FIELDS = {
    "audience": {"internal", "external", "public", "restricted"},
    "date_semantics": {
        "publication-date",
        "event-date",
        "effective-date",
        "recorded-date",
        "updated-date",
    },
    "ordering": {"newest-first", "oldest-first"},
    "insertion_rules": {"append", "prepend", "append-to-section", "prepend-to-section"},
    "sensitivity": {"public", "internal", "confidential", "restricted"},
    "visual_policy": {"caption-required", "caption-optional", "text-only", "no-visuals"},
    "correction_policy": {"append-correction", "append-only", "in-place-with-history", "immutable"},
}
_TRUST_ALLOWED_KEYS = {
    "profile_id",
    "target_fingerprint",
    "identity",
    "operations",
    "log_types",
    "audiences",
    "authorized_at",
    "expires_at",
    "revoked_at",
}
_TRUST_LIST_FIELDS = {"operations", "log_types", "audiences"}
_TRUSTABLE_OPERATIONS = {"append", "block_insert_after"}
_TRUST_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")


def _read_index(index_path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PrivateMemoryError("cannot read private memory index") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "profiles"}
        or type(payload["version"]) is not int
        or payload["version"] != 1
        or not isinstance(payload["profiles"], list)
        or not payload["profiles"]
    ):
        raise PrivateMemoryError("private memory index must use version 1 with a profiles list")
    return payload["profiles"]


def _iso8601(value: object, field: str, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise PrivateMemoryError(field + " must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PrivateMemoryError(field + " must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PrivateMemoryError(field + " must include an explicit UTC offset")
    return parsed


def _check_text(value: str, *, target_field: bool = False) -> None:
    if len(value) > 2000 or "```" in value or _CONTROL.search(value):
        raise PrivateMemoryError("private memory text contains unsafe formatting or control data")
    if _SENSITIVE.search(value):
        raise PrivateMemoryError("private memory entry contains sensitive value")
    if not target_field and _TARGET_LIKE.fullmatch(value):
        raise PrivateMemoryError("target-like values are only allowed in canonical target fields")


def _canonical_target(entry: dict[str, object]) -> str:
    canonical_url = str(entry["canonical_url"])
    parsed = urlsplit(canonical_url)
    domain = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (domain == "feishu.cn" or domain.endswith(".feishu.cn") or domain == "larksuite.com" or domain.endswith(".larksuite.com")):
        raise PrivateMemoryError("canonical URL must use an approved Feishu/Lark domain")
    if parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port:
        raise PrivateMemoryError("canonical URL must not contain query, fragment, credentials, or port")
    parts = parsed.path.split("/")
    if len(parts) != 3 or parts[0] or parts[1] not in {"docx", "wiki"} or not _TOKEN.fullmatch(parts[2]):
        raise PrivateMemoryError("canonical URL must contain exactly one docx/wiki target path")
    kind = str(entry["document_kind"])
    if kind not in {"docx", "wiki"} or parts[1] != kind:
        raise PrivateMemoryError("document_kind must match the canonical URL")
    document_token = entry["document_token"]
    if not isinstance(document_token, str) or not _TOKEN.fullmatch(document_token):
        raise PrivateMemoryError("document_token must be an opaque target token")
    if kind == "docx":
        if "wiki_node_token" in entry:
            raise PrivateMemoryError("docx profiles must not define wiki_node_token")
        if parts[2] != document_token:
            raise PrivateMemoryError("docx URL path must match document_token")
        return document_token
    wiki_token = entry.get("wiki_node_token")
    if not isinstance(wiki_token, str) or not _TOKEN.fullmatch(wiki_token) or parts[2] != wiki_token:
        raise PrivateMemoryError("wiki URL path must match wiki_node_token")
    return wiki_token


def _active_memory_path(entry: dict[str, object], memory_root: Path) -> Path:
    relative = Path(str(entry["memory_path"]))
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 2 or relative.parts[0] != "documents":
        raise PrivateMemoryError("memory_path must be a relative file under documents/")
    root = memory_root.resolve()
    active = (root / relative).resolve()
    try:
        active.relative_to((root / "documents").resolve())
    except ValueError as error:
        raise PrivateMemoryError("memory_path escapes documents/") from error
    if not active.is_file():
        raise PrivateMemoryError("memory_path must reference an existing regular file")
    return active


def _validate_active_memory(entry: dict[str, object], memory_root: Path) -> None:
    active = _active_memory_path(entry, memory_root)
    try:
        active_text = active.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PrivateMemoryError("active memory must be readable UTF-8 text") from error
    target_values = {str(entry["canonical_url"]), str(entry["document_token"])}
    if entry.get("wiki_node_token"):
        target_values.add(str(entry["wiki_node_token"]))
    if (
        any(value in active_text for value in target_values)
        or _ACTIVE_UNSAFE.search(active_text)
        or _ACTIVE_LEGACY_CLI.search(active_text)
        or _ACTIVE_TARGET_URL.search(active_text)
        or _ACTIVE_TARGET_FIELD.search(active_text)
        or _ACTIVE_CREDENTIAL_VALUE.search(active_text)
    ):
        raise PrivateMemoryError("active memory contains target or operational traces")


def _validate_entry(entry: object, memory_root: Path) -> tuple[dict[str, object], str]:
    if not isinstance(entry, dict) or set(entry) - _ALLOWED_KEYS or not _REQUIRED_KEYS <= set(entry):
        raise PrivateMemoryError("private memory entry does not match the complete schema")
    for key, value in entry.items():
        if key in _LIST_FIELDS:
            if (
                not isinstance(value, list)
                or (key == "log_types" and not value)
                or not all(isinstance(item, str) and item.strip() for item in value)
            ):
                raise PrivateMemoryError(key + " must be a string list with valid entries")
            if len(value) != len(set(value)):
                raise PrivateMemoryError(key + " items must be unique")
            for item in value:
                _check_text(item)
        elif key in _STRING_FIELDS:
            if not isinstance(value, str) or not value.strip():
                raise PrivateMemoryError(key + " must be a non-empty string")
            _check_text(
                value,
                target_field=key in {"canonical_url", "document_token", "wiki_node_token"}
                or key in _ENUM_FIELDS,
            )
        elif key == "last_verified_at":
            _iso8601(value, key, nullable=True)
    _iso8601(entry["migrated_at"], "migrated_at")
    profile_id = str(entry["profile_id"])
    if not _PROFILE_ID.fullmatch(profile_id):
        raise PrivateMemoryError("profile_id must be a lowercase selector of at most 64 characters")
    language = str(entry["language"])
    if not _LANGUAGE.fullmatch(language):
        raise PrivateMemoryError("language must be a BCP 47-style language tag")
    for field, allowed in _ENUM_FIELDS.items():
        if entry[field] not in allowed:
            raise PrivateMemoryError(field + " contains an unsupported value")
    try:
        ZoneInfo(str(entry["timezone"]))
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise PrivateMemoryError("timezone must be a valid IANA timezone") from error
    log_types = entry["log_types"]
    if not set(log_types) <= SUPPORTED_LOG_TYPES:
        raise PrivateMemoryError("log_types contains an unsupported value")
    target = _canonical_target(entry)
    _active_memory_path(entry, memory_root)
    return entry, target


def _validate_index_metadata(index_path: Path, memory_root: Path) -> list[dict[str, object]]:
    validated: list[dict[str, object]] = []
    selectors: set[str] = set()
    urls: set[str] = set()
    targets: set[str] = set()
    document_tokens: set[str] = set()
    wiki_node_tokens: set[str] = set()
    memory_paths: set[Path] = set()
    for raw in _read_index(Path(index_path)):
        entry, target = _validate_entry(raw, Path(memory_root))
        profile = str(entry["profile_id"])
        url = str(entry["canonical_url"])
        entry_aliases = set(entry["aliases"])
        entry_selectors = {profile, *entry_aliases}
        document_token = str(entry["document_token"])
        wiki_node_token = str(entry["wiki_node_token"]) if entry.get("wiki_node_token") else None
        memory_path = _active_memory_path(entry, Path(memory_root))
        if (
            len(entry_selectors) != 1 + len(entry_aliases)
            or selectors & entry_selectors
            or url in urls
            or target in targets
            or document_token in document_tokens
            or document_token in wiki_node_tokens
            or (wiki_node_token is not None and wiki_node_token in document_tokens)
            or wiki_node_token == document_token
            or memory_path in memory_paths
        ):
            raise PrivateMemoryError(
                "ambiguous private memory: selectors, targets, and backing tokens must be globally unique"
            )
        selectors.update(entry_selectors)
        urls.add(url)
        targets.add(target)
        document_tokens.add(document_token)
        if wiki_node_token is not None:
            wiki_node_tokens.add(wiki_node_token)
        memory_paths.add(memory_path)
        validated.append(entry)
    return validated


def validate_index(index_path: Path, memory_root: Path) -> list[dict[str, object]]:
    validated = _validate_index_metadata(index_path, memory_root)
    for entry in validated:
        _validate_active_memory(entry, Path(memory_root))
    return validated


def _entry_target(entry: dict[str, object]) -> str:
    return str(entry["wiki_node_token"] if entry["document_kind"] == "wiki" else entry["document_token"])


def profile_target_fingerprint(entry: dict[str, object]) -> str:
    components = [
        str(entry["profile_id"]),
        str(entry["canonical_url"]),
        str(entry["document_kind"]),
    ]
    if entry["document_kind"] == "wiki":
        components.append(str(entry["wiki_node_token"]))
    components.append(str(entry["document_token"]))
    payload = "\n".join(components)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_trust_policy(trust_path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(Path(trust_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PrivateMemoryError("cannot read trusted destination policy") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "grants"}
        or type(payload["version"]) is not int
        or payload["version"] != 1
        or not isinstance(payload["grants"], list)
        or not payload["grants"]
    ):
        raise PrivateMemoryError("trusted destination policy must use version 1 with a grants list")
    return payload["grants"]


def validate_trust_policy(
    trust_path: Path,
    entries: list[dict[str, object]],
) -> list[dict[str, object]]:
    profiles = {str(entry["profile_id"]): entry for entry in entries}
    validated: list[dict[str, object]] = []
    granted_profiles: set[str] = set()
    for raw in _read_trust_policy(Path(trust_path)):
        if not isinstance(raw, dict) or set(raw) != _TRUST_ALLOWED_KEYS:
            raise PrivateMemoryError("trusted destination grant does not match the complete schema")
        profile_id = raw["profile_id"]
        if not isinstance(profile_id, str) or profile_id not in profiles:
            raise PrivateMemoryError("trusted destination grant must reference an existing profile")
        if profile_id in granted_profiles:
            raise PrivateMemoryError("trusted destination grants must be unique by profile")
        profile = profiles[profile_id]
        fingerprint = raw["target_fingerprint"]
        if (
            not isinstance(fingerprint, str)
            or not _TRUST_FINGERPRINT.fullmatch(fingerprint)
            or fingerprint != profile_target_fingerprint(profile)
        ):
            raise PrivateMemoryError("trusted destination target fingerprint does not match the profile")
        if raw["identity"] != "user":
            raise PrivateMemoryError("trusted destination identity must be user")
        for field in _TRUST_LIST_FIELDS:
            value = raw[field]
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) and item for item in value)
                or len(value) != len(set(value))
            ):
                raise PrivateMemoryError(field + " must be a non-empty unique string list")
        if not set(raw["operations"]) <= _TRUSTABLE_OPERATIONS:
            raise PrivateMemoryError("trusted destination operations contain a destructive or unsupported action")
        if not set(raw["log_types"]) <= set(profile["log_types"]):
            raise PrivateMemoryError("trusted destination log types exceed the profile scope")
        if set(raw["audiences"]) != {profile["audience"]}:
            raise PrivateMemoryError("trusted destination audiences must match the profile audience")
        authorized_at = _iso8601(raw["authorized_at"], "authorized_at")
        expires_at = _iso8601(raw["expires_at"], "expires_at", nullable=True)
        revoked_at = _iso8601(raw["revoked_at"], "revoked_at", nullable=True)
        if expires_at is not None and expires_at <= authorized_at:
            raise PrivateMemoryError("expires_at must be later than authorized_at")
        if revoked_at is not None and revoked_at < authorized_at:
            raise PrivateMemoryError("revoked_at must not precede authorized_at")
        granted_profiles.add(profile_id)
        validated.append(raw)
    return validated


def _trust_status(
    entry: dict[str, object],
    entries: list[dict[str, object]],
    memory_root: Path,
    *,
    trust_path: Path | None,
    backing_document_token: str | None,
    explicit_write_intent: bool,
    operation: str | None,
    log_type: str | None,
    audience: str | None,
    identity: str | None,
    now: datetime | None,
) -> str:
    if entry["document_kind"] == "wiki":
        if backing_document_token is None:
            return "backing_target_required"
        if backing_document_token != entry["document_token"]:
            return "backing_target_mismatch"
    elif backing_document_token is not None:
        return "backing_target_not_applicable"
    policy_path = Path(trust_path) if trust_path is not None else Path(memory_root) / "trusted.json"
    if not policy_path.is_file():
        return "trust_not_configured"
    grants = validate_trust_policy(policy_path, entries)
    grant = next(
        (item for item in grants if item["profile_id"] == entry["profile_id"]),
        None,
    )
    if grant is None:
        return "trust_not_granted"
    if not explicit_write_intent:
        return "write_intent_required"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise PrivateMemoryError("trust evaluation time must include an explicit UTC offset")
    authorized_at = _iso8601(grant["authorized_at"], "authorized_at")
    expires_at = _iso8601(grant["expires_at"], "expires_at", nullable=True)
    revoked_at = _iso8601(grant["revoked_at"], "revoked_at", nullable=True)
    if current < authorized_at:
        return "trust_not_active"
    if revoked_at is not None and current >= revoked_at:
        return "trust_revoked"
    if expires_at is not None and current >= expires_at:
        return "trust_expired"
    if identity != grant["identity"]:
        return "identity_not_trusted"
    if operation not in grant["operations"]:
        return "operation_not_trusted"
    if log_type not in grant["log_types"] or audience not in grant["audiences"]:
        return "content_scope_not_trusted"
    return "trusted"


def validate_memory_index(
    index_path: Path,
    canonical_url: str | None,
    token: str | None,
    memory_root: Path,
    *,
    trust_path: Path | None = None,
    backing_document_token: str | None = None,
    explicit_write_intent: bool = False,
    operation: str | None = None,
    log_type: str | None = None,
    audience: str | None = None,
    identity: str | None = None,
    now: datetime | None = None,
) -> MemoryMatch:
    entries = _validate_index_metadata(index_path, memory_root)
    exact = [entry for entry in entries if entry["canonical_url"] == canonical_url and _entry_target(entry) == token]
    if len(exact) == 1:
        _validate_active_memory(exact[0], Path(memory_root))
        trust_status = _trust_status(
            exact[0],
            entries,
            Path(memory_root),
            trust_path=trust_path,
            backing_document_token=backing_document_token,
            explicit_write_intent=explicit_write_intent,
            operation=operation,
            log_type=log_type,
            audience=audience,
            identity=identity,
            now=now,
        )
        return MemoryMatch(
            trust_status == "trusted",
            "exact",
            Path(str(exact[0]["memory_path"])),
            trust_status,
        )
    if any(entry["canonical_url"] == canonical_url or _entry_target(entry) == token for entry in entries):
        raise PrivateMemoryError("canonical URL and target token must match the same entry exactly")
    if canonical_url is not None and token is None:
        aliases = [entry for entry in entries if canonical_url in entry["aliases"]]
        if len(aliases) == 1:
            _validate_active_memory(aliases[0], Path(memory_root))
            return MemoryMatch(
                False,
                "alias",
                Path(str(aliases[0]["memory_path"])),
                "candidate_only",
            )
    raise PrivateMemoryError("no private memory candidate matches the supplied target")


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(description="Validate or resolve an existing private-memory index without writing files.")
    parser.add_argument("operation", choices=("validate", "resolve", "trust-fingerprint"))
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--memory-root", required=True, type=Path)
    parser.add_argument("--trust", type=Path)
    parser.add_argument("--canonical-url")
    parser.add_argument("--target-token")
    parser.add_argument("--backing-document-token")
    parser.add_argument("--alias")
    parser.add_argument("--explicit-write-intent", action="store_true")
    parser.add_argument("--write-operation")
    parser.add_argument("--log-type", choices=sorted(SUPPORTED_LOG_TYPES))
    parser.add_argument("--audience", choices=sorted(_ENUM_FIELDS["audience"]))
    parser.add_argument("--identity", choices=("user", "bot"))
    try:
        args = parser.parse_args(argv)
        if args.operation == "validate":
            entries = validate_index(args.index, args.memory_root)
            policy_path = args.trust or (args.memory_root / "trusted.json")
            grants = (
                validate_trust_policy(policy_path, entries)
                if policy_path.is_file() or args.trust is not None
                else []
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "profiles": len(entries),
                        "trust_grants": len(grants),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        exact_supplied = bool(args.canonical_url or args.target_token)
        if args.alias and exact_supplied:
            raise PrivateMemoryError("--alias and exact target arguments are mutually exclusive")
        if args.operation == "trust-fingerprint":
            if args.alias or not (args.canonical_url and args.target_token):
                raise PrivateMemoryError(
                    "trust-fingerprint requires --canonical-url and --target-token"
                )
            entries = _validate_index_metadata(args.index, args.memory_root)
            exact = [
                entry
                for entry in entries
                if entry["canonical_url"] == args.canonical_url
                and _entry_target(entry) == args.target_token
            ]
            if len(exact) != 1:
                raise PrivateMemoryError(
                    "trust-fingerprint requires one exact canonical target"
                )
            if exact[0]["document_kind"] == "wiki":
                if args.backing_document_token != exact[0]["document_token"]:
                    raise PrivateMemoryError(
                        "trust-fingerprint requires the exact live backing document token for a wiki target"
                    )
            elif args.backing_document_token is not None:
                raise PrivateMemoryError(
                    "--backing-document-token only applies to wiki targets"
                )
            _validate_active_memory(exact[0], Path(args.memory_root))
            print(
                json.dumps(
                    {
                        "ok": True,
                        "profile_id": exact[0]["profile_id"],
                        "target_fingerprint": profile_target_fingerprint(exact[0]),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.alias:
            match = validate_memory_index(args.index, args.alias, None, args.memory_root)
        elif args.canonical_url and args.target_token:
            match = validate_memory_index(
                args.index,
                args.canonical_url,
                args.target_token,
                args.memory_root,
                trust_path=args.trust,
                backing_document_token=args.backing_document_token,
                explicit_write_intent=args.explicit_write_intent,
                operation=args.write_operation,
                log_type=args.log_type,
                audience=args.audience,
                identity=args.identity,
            )
        else:
            raise PrivateMemoryError("resolve requires --alias or both --canonical-url and --target-token")
    except (PrivateMemoryError, OSError, UnicodeError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    data = asdict(match)
    data["memory_path"] = str(data["memory_path"]) if data["memory_path"] else None
    print(json.dumps({"ok": True, "match": data}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
