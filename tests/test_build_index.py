import base64
import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "build_index.py"
SPEC = importlib.util.spec_from_file_location("build_index", MODULE_PATH)
build_index = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_index)


REPOSITORY = {
    "full_name": "tui-lipan/vim-rozi-navigator",
    "clone_url": "https://github.com/tui-lipan/vim-rozi-navigator.git",
    "default_branch": "master",
    "private": False,
    "archived": False,
    "disabled": False,
    "fork": False,
    "stargazers_count": 3,
    "updated_at": "2026-09-21T00:00:00Z",
}


class FakeClient:
    def __init__(self, manifest: str, extra=()) -> None:
        self.manifest = manifest
        self.extra = list(extra)

    def get(self, path, query=None):
        if path == "/search/repositories":
            return {"items": [REPOSITORY, *self.extra]}
        if path.endswith("/commits/master"):
            return {"sha": "a" * 40}
        if path.endswith("/contents/extension.toml"):
            return {
                "encoding": "base64",
                "content": base64.b64encode(self.manifest.encode()).decode(),
            }
        raise AssertionError(path)


MANIFEST = """
[extension]
id = "vim-rozi-navigator"
title = "Vim and Neovim navigator"
description = "Split-aware navigation"
version = "0.2.1"
api = 1
min_rozi = "0.0.16"
homepage = "https://github.com/tui-lipan/vim-rozi-navigator"

[[navigation_targets]]
name = "vim"
programs = ["vim", "nvim"]

[[suggested_keybindings]]
action = "smart-focus-left"
key = "ctrl-h"
"""


class BuildIndexTests(unittest.TestCase):
    def test_builds_a_deterministic_entry_from_default_branch(self):
        document, skipped = build_index.build(
            FakeClient(MANIFEST), datetime(2026, 9, 21, tzinfo=timezone.utc)
        )
        self.assertEqual(skipped["skipped"], [])
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["generated_at"], "2026-09-21T00:00:00Z")
        self.assertEqual(document["extensions"][0]["commit"], "a" * 40)
        self.assertEqual(document["extensions"][0]["navigation_targets"], 1)
        self.assertEqual(document["extensions"][0]["suggested_keybindings"], 1)

    def test_skips_manifest_without_catalog_metadata(self):
        document, skipped = build_index.build(
            FakeClient("[extension]\nid = 'x'\napi = 1\n"),
            datetime(2026, 9, 21, tzinfo=timezone.utc),
        )
        self.assertEqual(document["extensions"], [])
        self.assertEqual(
            skipped["skipped"],
            [
                {
                    "repository": "tui-lipan/vim-rozi-navigator",
                    "reason": "extension.title must be a non-empty string",
                }
            ],
        )

    def test_publishes_why_each_repository_was_left_out_but_never_names_a_private_one(self):
        def repository(name, **states):
            return {**REPOSITORY, "full_name": f"owner/{name}", **states}

        document, report = build_index.build(
            FakeClient(
                MANIFEST,
                [
                    repository("hidden", private=True),
                    repository("old", archived=True),
                    repository("copy", fork=True, archived=True),
                ],
            ),
            datetime(2026, 9, 21, tzinfo=timezone.utc),
        )
        self.assertEqual(len(document["extensions"]), 1)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["generated_at"], document["generated_at"])
        self.assertEqual(
            report["skipped"],
            [
                {"repository": "owner/copy", "reason": "repository is archived and a fork"},
                {"repository": "owner/old", "reason": "repository is archived"},
            ],
        )
        self.assertNotIn(b"hidden", build_index.serialize(report))

    def test_a_long_reason_is_kept_to_one_bounded_line(self):
        reason = build_index.short_reason("GitHub returned 500:\n" + "x" * 1000)
        self.assertEqual(len(reason), build_index.MAX_REASON_CHARS)
        self.assertNotIn("\n", reason)
        self.assertTrue(reason.endswith("…"))

    def test_rejects_unsupported_platform(self):
        with self.assertRaisesRegex(ValueError, "unsupported platforms"):
            build_index.catalog_entry(
                {
                    "full_name": "owner/repo",
                    "clone_url": "https://github.com/owner/repo.git",
                    "stargazers_count": 0,
                    "updated_at": "2026-09-21T00:00:00Z",
                },
                "b" * 40,
                MANIFEST.replace(
                    'api = 1\n', 'api = 1\nplatforms = ["plan9"]\n'
                ).encode(),
            )

    def entry_with(self, replacement):
        return build_index.catalog_entry(
            {
                "full_name": "owner/repo",
                "clone_url": "https://github.com/owner/repo.git",
                "stargazers_count": 0,
                "updated_at": "2026-09-21T00:00:00Z",
            },
            "b" * 40,
            MANIFEST.replace(*replacement).encode(),
        )

    def test_rejects_homepages_rozi_cannot_parse(self):
        # Rozi drops a record it cannot read, so the generator must refuse the same values.
        for homepage in ["https://", "http:///path", "https://exa mple.com", "https://user@host", "https://host:99999", "ftp://host"]:
            with self.subTest(homepage=homepage):
                with self.assertRaisesRegex(ValueError, "homepage"):
                    self.entry_with(
                        ('homepage = "https://github.com/tui-lipan/vim-rozi-navigator"', f'homepage = "{homepage}"')
                    )
        entry = self.entry_with(("github.com/tui-lipan", "example.org:8080/tui-lipan"))
        self.assertEqual(entry["homepage"], "https://example.org:8080/tui-lipan/vim-rozi-navigator")

    def test_rejects_versions_rozi_cannot_parse(self):
        for version in ["1.0.0-01", "1.0.0-a..b", "1.0.0-", "1.0.0+", "01.0.0"]:
            with self.subTest(version=version):
                with self.assertRaisesRegex(ValueError, "min_rozi"):
                    self.entry_with(('min_rozi = "0.0.16"', f'min_rozi = "{version}"'))
        entry = self.entry_with(('min_rozi = "0.0.16"', 'min_rozi = "1.0.0-rc.1+build.5"'))
        self.assertEqual(entry["min_rozi"], "1.0.0-rc.1+build.5")

    def test_rejects_boolean_api(self):
        with self.assertRaisesRegex(ValueError, "api"):
            self.entry_with(("api = 1", "api = true"))

    def test_rejects_overlong_metadata(self):
        for field, limit in build_index.MAX_FIELD_CHARS.items():
            if field in {"id", "min_rozi", "homepage"}:
                continue
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, f"extension.{field} is longer than {limit}"):
                    self.entry_with((f'{field} = "', f'{field} = "{"x" * limit}'))
        with self.assertRaisesRegex(ValueError, "longer than 256"):
            self.entry_with(("github.com/tui-lipan", "github.com/" + "a" * 256))
        entry = self.entry_with(('description = "Split-aware navigation"', f'description = "{"d" * 280}"'))
        self.assertEqual(len(entry["description"]), 280)

    def test_sheds_least_established_entries_to_fit_rozi_limit(self):
        def entry(index, stars):
            return {
                "repository": f"owner/repo-{index}",
                "title": f"Title {index:04}",
                "stars": stars,
                "updated_at": "2026-09-21T00:00:00Z",
                "description": "d" * 280,
            }

        size = len(build_index.serialize({"extensions": [entry(0, 0)]}))
        count = 2 * build_index.MAX_INDEX_BYTES // size
        document = {
            "schema_version": 1,
            "generated_at": "2026-09-21T00:00:00Z",
            "extensions": [entry(index, index) for index in range(count)],
        }
        dropped = build_index.fit_size_limit(document)
        self.assertTrue(dropped)
        self.assertIn("index would exceed", dropped[0][1])
        self.assertLessEqual(len(build_index.serialize(document)), build_index.MAX_INDEX_BYTES)
        kept = {item["stars"] for item in document["extensions"]}
        self.assertEqual(min(kept), len(dropped), "the fewest-star entries go first")

    def test_writes_compact_ascii_json(self):
        document, _ = build_index.build(FakeClient(MANIFEST), datetime(2026, 9, 21, tzinfo=timezone.utc))
        text = build_index.serialize(document)
        self.assertNotIn(b"\n ", text)
        self.assertTrue(text.isascii())


if __name__ == "__main__":
    unittest.main()
