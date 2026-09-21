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


class FakeClient:
    def __init__(self, manifest: str) -> None:
        self.manifest = manifest

    def get(self, path, query=None):
        if path == "/search/repositories":
            return {
                "items": [
                    {
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
                ]
            }
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
        self.assertEqual(skipped, [])
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
        self.assertIn("extension.title", skipped[0])

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


if __name__ == "__main__":
    unittest.main()
