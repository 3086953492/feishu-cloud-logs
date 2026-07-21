import importlib
import json
import tempfile
import unittest
from pathlib import Path


DOCUMENT_TOKEN = "dox" + "cnTestToken000000000"
CANONICAL_URL = "https://example." + "feishu.cn/docx/" + DOCUMENT_TOKEN


class PrivateMemoryContractTests(unittest.TestCase):
    def memory(self):
        return importlib.import_module("scripts.private_memory")

    def write_index(self, root, entries):
        index = root / ".local" / "index.json"
        index.parent.mkdir(exist_ok=True)
        for entry in entries:
            relative = Path(str(entry.get("memory_path", "")))
            if relative.parts and relative.parts[0] == "documents" and ".." not in relative.parts:
                memory_file = root / relative
                memory_file.parent.mkdir(parents=True, exist_ok=True)
                memory_file.write_text("# profile\n", encoding="utf-8")
        index.write_text(json.dumps({"version": 1, "profiles": entries}), encoding="utf-8")
        return index

    def exact_entry(self, **overrides):
        entry = {
            "canonical_url": CANONICAL_URL,
            "document_token": DOCUMENT_TOKEN,
            "memory_path": "documents/cloud-log.md",
            "aliases": ["weekly-log"],
            "profile_id": "logs-default",
            "document_kind": "docx",
            "log_types": ["engineering"],
            "audience": "internal",
            "language": "zh-CN",
            "timezone": "Asia/Shanghai",
            "date_semantics": "publication-date",
            "ordering": "newest-first",
            "stable_headings": ["Summary"],
            "insertion_rules": "append",
            "protected_regions": ["History"],
            "sensitivity": "internal",
            "visual_policy": "caption-required",
            "correction_policy": "append-correction",
            "last_verified_at": "2026-07-18T00:00:00Z",
            "migrated_at": "2026-07-18T12:00:00+08:00",
        }
        entry.update(overrides)
        return entry

    def test_accepts_an_ignored_local_index_with_exact_canonical_url_and_token(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.write_index(root, [self.exact_entry()])

            match = memory.validate_memory_index(
                index_path=index,
                canonical_url=CANONICAL_URL,
                token=DOCUMENT_TOKEN,
                memory_root=root,
            )

        self.assertTrue(match.may_mutate)
        self.assertEqual("exact", match.match_kind)
        self.assertEqual(Path("documents/cloud-log.md"), match.memory_path)

    def test_every_declared_correction_policy_is_usable(self):
        memory = self.memory()
        for policy in (
            "append-correction",
            "append-only",
            "in-place-with-history",
            "immutable",
        ):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                index = self.write_index(
                    root,
                    [self.exact_entry(correction_policy=policy)],
                )

                entries = memory.validate_index(index, root)

            self.assertEqual(policy, entries[0]["correction_policy"])

    def test_alias_only_candidate_does_not_authorize_a_mutation(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.write_index(root, [self.exact_entry()])

            match = memory.validate_memory_index(
                index_path=index,
                canonical_url="weekly-" + "log",
                token=None,
                memory_root=root,
            )

        self.assertFalse(match.may_mutate)
        self.assertEqual("alias", match.match_kind)

    def test_rejects_candidates_when_either_exact_url_or_token_does_not_match(self):
        memory = self.memory()
        candidates = (
            (CANONICAL_URL + "-wrong", DOCUMENT_TOKEN),
            (CANONICAL_URL, DOCUMENT_TOKEN + "-wrong"),
        )

        for canonical_url, token in candidates:
            with self.subTest(canonical_url=canonical_url, token=token):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    index = self.write_index(root, [self.exact_entry()])

                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_memory_index(
                            index_path=index,
                            canonical_url=canonical_url,
                            token=token,
                            memory_root=root,
                        )

    def test_rejects_ambiguous_exact_candidates(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.write_index(
                root,
                [self.exact_entry(memory_path="documents/one.md"), self.exact_entry(memory_path="documents/two.md")],
            )

            with self.assertRaises(memory.PrivateMemoryError) as raised:
                memory.validate_memory_index(
                    index_path=index,
                    canonical_url=CANONICAL_URL,
                    token=DOCUMENT_TOKEN,
                    memory_root=root,
                )

        self.assertIn("ambiguous", str(raised.exception).lower())

    def test_rejects_private_entries_with_sensitive_metadata_or_traversal_paths(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unsafe_entries = [
                self.exact_entry(api_key="secret-value"),
                self.exact_entry(block_id="blk_123"),
                self.exact_entry(revision_id="rev_123"),
                self.exact_entry(owner_email="person@example.com"),
                self.exact_entry(memory_path="../../outside.md"),
                self.exact_entry(memory_path=str((root / "outside.md").resolve())),
            ]

            for entry in unsafe_entries:
                with self.subTest(entry=entry):
                    index = self.write_index(root, [entry])

                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_memory_index(
                            index_path=index,
                            canonical_url=CANONICAL_URL,
                            token=DOCUMENT_TOKEN,
                            memory_root=root,
                        )

    def test_requires_the_versioned_index_shape(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = root / ".local" / "index.json"
            index.parent.mkdir()
            index.write_text(json.dumps({"profiles": [self.exact_entry()]}), encoding="utf-8")

            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_memory_index(
                    index_path=index,
                    canonical_url=CANONICAL_URL,
                    token=DOCUMENT_TOKEN,
                    memory_root=root,
                )

    def test_rejects_owner_identifier_and_phone_number_metadata(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for entry in (
                self.exact_entry(owner_id="ou_" + "1234567890abcdef"),
                self.exact_entry(contact_phone="13800138000"),
            ):
                with self.subTest(entry=entry):
                    index = self.write_index(root, [entry])
                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_memory_index(index, CANONICAL_URL, DOCUMENT_TOKEN, root)

    def test_rejects_sensitive_values_recursively_even_in_target_or_alias_fields(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for entry in (
                self.exact_entry(canonical_url=CANONICAL_URL + "?token=sk-live-12345678901234567890"),
                self.exact_entry(token="sk-live-12345678901234567890"),
                self.exact_entry(aliases=["person@example.com"]),
                self.exact_entry(protected_regions=["owner ou_" + "1234567890abcdef"]),
            ):
                with self.subTest(entry=entry):
                    index = self.write_index(root, [entry])
                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_memory_index(index, CANONICAL_URL, DOCUMENT_TOKEN, root)

    def test_rejects_noncanonical_targets_and_query_or_fragment_urls(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for entry in (
                self.exact_entry(canonical_url="https://example.com/docx/" + DOCUMENT_TOKEN),
                self.exact_entry(canonical_url=CANONICAL_URL + "?foo=bar"),
                self.exact_entry(canonical_url=CANONICAL_URL + "#section"),
                self.exact_entry(token="not-a-feishu-token"),
            ):
                with self.subTest(entry=entry):
                    index = self.write_index(root, [entry])
                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_memory_index(index, CANONICAL_URL, DOCUMENT_TOKEN, root)

    def test_cli_validate_does_not_require_a_target_while_resolve_does(self):
        memory = self.memory()
        from contextlib import redirect_stdout
        from io import StringIO
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.write_index(root, [self.exact_entry()])
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, memory.main(["validate", "--index", str(index), "--memory-root", str(root)]))
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(1, payload["profiles"])
            self.assertNotIn("documents", payload)
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(2, memory.main(["resolve", "--index", str(index), "--memory-root", str(root)]))
            self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_requires_every_planned_index_field_and_rejects_token_outside_target_fields(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing_policy = self.exact_entry()
            del missing_policy["correction_policy"]
            missing_migration_time = self.exact_entry()
            del missing_migration_time["migrated_at"]
            for entry in (
                missing_policy,
                missing_migration_time,
                self.exact_entry(profile_id=DOCUMENT_TOKEN),
            ):
                with self.subTest(entry=entry):
                    index = self.write_index(root, [entry])
                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_memory_index(index, CANONICAL_URL, DOCUMENT_TOKEN, root)

    def test_wiki_target_matches_wiki_node_token_while_retaining_backing_docx_token(self):
        memory = self.memory()
        wiki_token = "wikiTestNode000000000"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = self.exact_entry(
                canonical_url="https://example." + "feishu.cn/wiki/" + wiki_token,
                document_token=DOCUMENT_TOKEN,
                wiki_node_token=wiki_token,
                document_kind="wiki",
            )
            index = self.write_index(root, [entry])
            match = memory.validate_memory_index(index, entry["canonical_url"], wiki_token, root)

        self.assertTrue(match.may_mutate)

    def test_document_kind_enforces_wiki_node_token_presence(self):
        memory = self.memory()
        wiki_token = "wikiTestNode000000000"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docx_entry = self.exact_entry(wiki_node_token=wiki_token)
            index = self.write_index(root, [docx_entry])
            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_index(index, root)

            wiki_entry = self.exact_entry(
                canonical_url="https://example." + "feishu.cn/wiki/" + wiki_token,
                document_kind="wiki",
            )
            index = self.write_index(root, [wiki_entry])
            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_index(index, root)

    def test_accepts_opaque_legacy_and_modern_target_tokens(self):
        memory = self.memory()
        for document_token in ("AbCdEfGhIjKlMnOpQrStUvWxYz1", "modern_target-Token_2026"):
            with self.subTest(document_token=document_token), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                entry = self.exact_entry(
                    document_token=document_token,
                    canonical_url="https://example." + "feishu.cn/docx/" + document_token,
                )
                index = self.write_index(root, [entry])
                match = memory.validate_memory_index(index, entry["canonical_url"], document_token, root)
                self.assertTrue(match.may_mutate)

    def test_alias_cli_is_non_authorizing_and_mutually_exclusive_with_exact_target(self):
        memory = self.memory()
        from contextlib import redirect_stdout
        from io import StringIO
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.write_index(root, [self.exact_entry()])
            output = StringIO()
            with redirect_stdout(output):
                code = memory.main(["resolve", "--index", str(index), "--memory-root", str(root), "--alias", "weekly-log"])
            self.assertEqual(0, code)
            self.assertFalse(json.loads(output.getvalue())["match"]["may_mutate"])

            output = StringIO()
            with redirect_stdout(output):
                code = memory.main(["resolve", "--index", str(index), "--memory-root", str(root), "--alias", "weekly-log", "--canonical-url", CANONICAL_URL, "--target-token", DOCUMENT_TOKEN])
            self.assertEqual(2, code)

    def test_rejects_global_profile_target_and_alias_ambiguity(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = self.exact_entry()
            collisions = (
                self.exact_entry(profile_id=base["profile_id"], canonical_url="https://example." + "feishu.cn/docx/" + "Other" + "Token123", document_token="Other" + "Token123", aliases=["other"], memory_path="documents/other.md"),
                self.exact_entry(profile_id="other", aliases=base["aliases"], canonical_url="https://example." + "feishu.cn/docx/" + "Other" + "Token123", document_token="Other" + "Token123", memory_path="documents/other.md"),
                self.exact_entry(profile_id="other", aliases=["other"], memory_path="documents/other.md"),
            )
            for other in collisions:
                with self.subTest(other=other):
                    index = self.write_index(root, [base, other])
                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_index(index, root)

    def test_memory_path_must_be_existing_regular_file_under_documents(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in ("profile.md", "archive/profile.md", "documents/missing.md"):
                with self.subTest(path=path):
                    entry = self.exact_entry(memory_path=path)
                    index = self.write_index(root, [entry])
                    if path == "documents/missing.md":
                        (root / path).unlink()
                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_index(index, root)

    def test_allows_unverified_migration_timestamp_and_rejects_unsafe_free_text(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            safe = self.exact_entry(last_verified_at=None, migrated_at="2026-07-18T12:00:00+08:00")
            index = self.write_index(root, [safe])
            self.assertEqual(1, len(memory.validate_index(index, root)))
            for entry in (
                self.exact_entry(migrated_at="not-a-date"),
                self.exact_entry(insertion_rules="```unsafe```"),
                self.exact_entry(stable_headings=["bad\x00heading"]),
                self.exact_entry(correction_policy="x" * 2001),
                self.exact_entry(log_types=["weekly"]),
                self.exact_entry(document_kind="sheet"),
            ):
                with self.subTest(entry=entry):
                    index = self.write_index(root, [entry])
                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_index(index, root)

    def test_rejects_operational_or_target_traces_inside_active_memory(self):
        memory = self.memory()
        unsafe_fragments = (
            "https://example." + "feishu.cn/docx/" + DOCUMENT_TOKEN,
            DOCUMENT_TOKEN,
            "mcp" + "__legacy_feishu",
            "npx " + "@larksuite/" + "cli@2.3.1",
            "lark" + "-cli docs update",
            "block_" + "id: value",
            "revision_" + "id: value",
            "owner_" + "id: value",
            "raw " + "api response",
            "auth" + "orization: bearer value",
            "person" + "@example.com",
        )
        for fragment in unsafe_fragments:
            with self.subTest(fragment=fragment), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                entry = self.exact_entry()
                index = self.write_index(root, [entry])
                (root / entry["memory_path"]).write_text("# Writing guidance\n\n" + fragment, encoding="utf-8")
                with self.assertRaises(memory.PrivateMemoryError):
                    memory.validate_index(index, root)

    def test_rejects_password_cookie_session_auth_and_formatted_phone_values(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for entry in (
                self.exact_entry(aliases=["password=abc"]),
                self.exact_entry(protected_regions=["cookie=session-value"]),
                self.exact_entry(stable_headings=["138-0013-8000"]),
                self.exact_entry(log_types=["person@example.com"]),
            ):
                with self.subTest(entry=entry):
                    index = self.write_index(root, [entry])
                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_memory_index(index, CANONICAL_URL, DOCUMENT_TOKEN, root)

    def test_index_requires_exact_top_level_keys_and_at_least_one_profile(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.write_index(root, [self.exact_entry()])
            payload = json.loads(index.read_text(encoding="utf-8"))
            payload["note"] = "not part of the index contract"
            index.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_index(index, root)

            index.write_text(json.dumps({"version": 1, "profiles": []}), encoding="utf-8")
            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_index(index, root)

            index.write_text(
                json.dumps({"version": True, "profiles": [self.exact_entry()]}),
                encoding="utf-8",
            )
            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_index(index, root)

    def test_rejects_the_legacy_documents_top_level_key(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = self.exact_entry()
            index = self.write_index(root, [entry])
            index.write_text(
                json.dumps({"version": 1, "documents": [entry]}),
                encoding="utf-8",
            )

            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_index(index, root)

    def test_migrated_at_is_required_and_must_be_an_aware_iso8601_timestamp(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = self.exact_entry()
            del missing["migrated_at"]
            for entry in (
                missing,
                self.exact_entry(migrated_at=None),
                self.exact_entry(migrated_at="2026-07-18T12:00:00"),
            ):
                with self.subTest(entry=entry):
                    index = self.write_index(root, [entry])
                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_index(index, root)

            index = self.write_index(root, [self.exact_entry()])
            self.assertEqual(1, len(memory.validate_index(index, root)))

    def test_profile_id_must_be_a_nonempty_lowercase_selector(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for profile_id in ("", " ", "Logs-Default", "logs/default", "logs--default"):
                with self.subTest(profile_id=profile_id):
                    index = self.write_index(root, [self.exact_entry(profile_id=profile_id)])
                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_index(index, root)

    def test_timestamps_must_include_an_explicit_utc_offset(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for field in ("last_verified_at", "migrated_at"):
                for value in ("2026-07-18", "2026-07-18T12:00:00"):
                    with self.subTest(field=field, value=value):
                        index = self.write_index(root, [self.exact_entry(**{field: value})])
                        with self.assertRaises(memory.PrivateMemoryError):
                            memory.validate_index(index, root)

    def test_timezone_must_be_a_real_iana_zone(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.write_index(root, [self.exact_entry(timezone="Mars/Olympus_Mons")])
            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_index(index, root)

    def test_rejects_unknown_or_empty_profile_domain_values(self):
        memory = self.memory()
        invalid_values = {
            "audience": ("unknown-audience", ""),
            "language": ("not a locale", ""),
            "date_semantics": ("whenever", ""),
            "ordering": ("sideways", ""),
            "insertion_rules": ("overwrite-everything", ""),
            "sensitivity": ("secret-ish", ""),
            "visual_policy": ("looks-good", ""),
            "correction_policy": ("silently-rewrite", ""),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for field, values in invalid_values.items():
                for value in values:
                    with self.subTest(field=field, value=value):
                        index = self.write_index(root, [self.exact_entry(**{field: value})])
                        with self.assertRaises(memory.PrivateMemoryError):
                            memory.validate_index(index, root)

    def test_optional_lists_may_be_empty_but_log_types_may_not(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for field in ("aliases", "stable_headings", "protected_regions"):
                with self.subTest(field=field):
                    index = self.write_index(root, [self.exact_entry(**{field: []})])
                    self.assertEqual(1, len(memory.validate_index(index, root)))

            index = self.write_index(root, [self.exact_entry(log_types=[])])
            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_index(index, root)

    def test_backing_document_tokens_are_globally_unique_for_wiki_profiles(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wiki_one = "WikiNodeTarget000000001"
            wiki_two = "WikiNodeTarget000000002"
            entries = [
                self.exact_entry(
                    canonical_url="https://example." + "feishu.cn/wiki/" + wiki_one,
                    wiki_node_token=wiki_one,
                    document_kind="wiki",
                    profile_id="wiki-one",
                    aliases=["first-wiki"],
                    memory_path="documents/wiki-one.md",
                ),
                self.exact_entry(
                    canonical_url="https://example." + "feishu.cn/wiki/" + wiki_two,
                    wiki_node_token=wiki_two,
                    document_kind="wiki",
                    profile_id="wiki-two",
                    aliases=["second-wiki"],
                    memory_path="documents/wiki-two.md",
                ),
            ]
            index = self.write_index(root, entries)
            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_index(index, root)

    def test_wiki_node_tokens_cannot_collide_with_any_backing_document_token(self):
        memory = self.memory()
        wiki_target = "WikiNodeTarget000000003"
        other_wiki_target = "WikiNodeTarget000000004"
        entries = [
            self.exact_entry(
                canonical_url="https://example." + "feishu.cn/wiki/" + wiki_target,
                wiki_node_token=wiki_target,
                document_kind="wiki",
                profile_id="wiki-three",
                aliases=["third-wiki"],
                memory_path="documents/wiki-three.md",
            ),
            self.exact_entry(
                canonical_url="https://example." + "feishu.cn/wiki/" + other_wiki_target,
                document_token=wiki_target,
                wiki_node_token=other_wiki_target,
                document_kind="wiki",
                profile_id="wiki-four",
                aliases=["fourth-wiki"],
                memory_path="documents/wiki-four.md",
            ),
        ]
        for ordered_entries in (entries, list(reversed(entries))):
            with self.subTest(order=[entry["profile_id"] for entry in ordered_entries]), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                index = self.write_index(root, ordered_entries)
                with self.assertRaises(memory.PrivateMemoryError):
                    memory.validate_index(index, root)

    def test_profile_ids_and_aliases_share_one_global_selector_space(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = self.exact_entry()
            variants = (
                self.exact_entry(
                    canonical_url="https://example." + "feishu.cn/docx/" + "Other" + "Target" + "000000001",
                    document_token="Other" + "Target" + "000000001",
                    profile_id="weekly-log",
                    aliases=["other-one"],
                    memory_path="documents/other-one.md",
                ),
                self.exact_entry(
                    canonical_url="https://example." + "feishu.cn/docx/" + "Other" + "Target" + "000000002",
                    document_token="Other" + "Target" + "000000002",
                    profile_id="other-two",
                    aliases=["logs-default"],
                    memory_path="documents/other-two.md",
                ),
            )
            for other in variants:
                with self.subTest(other=other):
                    index = self.write_index(root, [base, other])
                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_index(index, root)

            index = self.write_index(root, [self.exact_entry(aliases=["logs-default"])])
            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_index(index, root)

    def test_active_memory_rejects_any_target_url_or_target_field_trace(self):
        memory = self.memory()
        unsafe_fragments = (
            "https://another." + "larksuite.com/docx/OtherOpaqueTarget123",
            "canonical_" + "url: private target",
            "document_" + "token: OtherOpaqueTarget123",
            "document " + "token: OtherOpaqueTarget123",
            "wiki_node_" + "token: OtherOpaqueTarget123",
            "wiki-node-" + "token: OtherOpaqueTarget123",
            "target_" + "token: OtherOpaqueTarget123",
            "canonical" + "Url: private target",
        )
        for fragment in unsafe_fragments:
            with self.subTest(fragment=fragment), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                entry = self.exact_entry()
                index = self.write_index(root, [entry])
                (root / entry["memory_path"]).write_text(
                    "# Writing guidance\n\n" + fragment,
                    encoding="utf-8",
                )
                with self.assertRaises(memory.PrivateMemoryError):
                    memory.validate_index(index, root)

    def test_resolve_reads_only_the_selected_profile_while_validate_reads_all(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            other_token = "OtherTarget0000000000002"
            primary = self.exact_entry()
            other = self.exact_entry(
                canonical_url="https://example." + "larksuite.com/docx/" + other_token,
                document_token=other_token,
                profile_id="logs-other",
                aliases=["other-log"],
                memory_path="documents/other-log.md",
            )
            index = self.write_index(root, [primary, other])
            (root / other["memory_path"]).write_text(
                "# Guidance\n\n" + "document_" + "token: hidden",
                encoding="utf-8",
            )

            primary_match = memory.validate_memory_index(
                index,
                primary["canonical_url"],
                primary["document_token"],
                root,
            )
            self.assertTrue(primary_match.may_mutate)

            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_index(index, root)
            from contextlib import redirect_stdout
            from io import StringIO

            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    2,
                    memory.main(
                        [
                            "validate",
                            "--index",
                            str(index),
                            "--memory-root",
                            str(root),
                        ]
                    ),
                )
            self.assertFalse(json.loads(output.getvalue())["ok"])
            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_memory_index(
                    index,
                    other["canonical_url"],
                    other["document_token"],
                    root,
                )

    def test_resolved_memory_paths_are_globally_unique_across_profiles(self):
        memory = self.memory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            other_token = "OtherTarget0000000000003"
            entries = [
                self.exact_entry(memory_path="documents/cloud-log.md"),
                self.exact_entry(
                    canonical_url="https://example." + "larksuite.com/docx/" + other_token,
                    document_token=other_token,
                    profile_id="logs-other",
                    aliases=["other-log"],
                    memory_path="documents/./cloud-log.md",
                ),
            ]
            index = self.write_index(root, entries)

            with self.assertRaises(memory.PrivateMemoryError):
                memory.validate_index(index, root)

    def test_active_memory_rejects_credential_values_but_allows_security_prose(self):
        memory = self.memory()
        unsafe_fragments = (
            "api_" + "key = private-value-123",
            "secret" + ": actual-secret-value",
            "credential" + "=private-credential-value",
            "sk-" + "liveCredentialValue123456",
            "pk-" + "liveCredentialValue123456",
        )
        for fragment in unsafe_fragments:
            with self.subTest(fragment=fragment), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                entry = self.exact_entry()
                index = self.write_index(root, [entry])
                (root / entry["memory_path"]).write_text(
                    "# Writing guidance\n\n" + fragment,
                    encoding="utf-8",
                )
                with self.assertRaises(memory.PrivateMemoryError):
                    memory.validate_index(index, root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = self.exact_entry()
            index = self.write_index(root, [entry])
            (root / entry["memory_path"]).write_text(
                "# Writing guidance\n\n不要泄露 " + "secret，也不要记录 credential。",
                encoding="utf-8",
            )
            self.assertEqual(1, len(memory.validate_index(index, root)))

    def test_rejects_duplicate_items_inside_every_list_field(self):
        memory = self.memory()
        duplicates = {
            "aliases": ["weekly-log", "weekly-log"],
            "log_types": ["engineering", "engineering"],
            "stable_headings": ["Summary", "Summary"],
            "protected_regions": ["History", "History"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for field, values in duplicates.items():
                with self.subTest(field=field):
                    index = self.write_index(root, [self.exact_entry(**{field: values})])
                    with self.assertRaises(memory.PrivateMemoryError):
                        memory.validate_index(index, root)


if __name__ == "__main__":
    unittest.main()
