#!/usr/bin/env python3
"""Build the public Rozi extension index without executing repository code."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_ROOT = "https://api.github.com"
ALLOWED_PLATFORMS = {"linux", "macos", "windows", "freebsd", "netbsd"}
ID_PATTERN = re.compile(r"^[a-z0-9_-]+$")
SEMVER_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$"
)
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class GitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def get(self, path: str, query: dict[str, str | int] | None = None) -> Any:
        url = f"{API_ROOT}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "rozi-extension-index",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise RuntimeError(f"GitHub returned {error.code} for {url}: {detail}") from error


def repositories(client: GitHubClient) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for page in range(1, 11):
        document = client.get(
            "/search/repositories",
            {"q": "topic:rozi-extension", "sort": "updated", "order": "desc", "per_page": 100, "page": page},
        )
        items = document.get("items", [])
        found.extend(items)
        if len(items) < 100:
            break
    return found


def required_string(table: dict[str, Any], key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"extension.{key} must be a non-empty string")
    return value.strip()


def optional_string(table: dict[str, Any], key: str) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"extension.{key} must be a non-empty string when present")
    return value.strip()


def contribution_count(manifest: dict[str, Any], key: str) -> int:
    value = manifest.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{key} must be an array")
    return len(value)


def catalog_entry(repo: dict[str, Any], commit: str, manifest_bytes: bytes) -> dict[str, Any]:
    if not COMMIT_PATTERN.fullmatch(commit):
        raise ValueError("default branch did not resolve to a full Git commit")
    manifest = tomllib.loads(manifest_bytes.decode("utf-8"))
    extension = manifest.get("extension")
    if not isinstance(extension, dict):
        raise ValueError("extension.toml must contain [extension]")

    extension_id = required_string(extension, "id")
    if not ID_PATTERN.fullmatch(extension_id):
        raise ValueError("extension.id is invalid")
    api = extension.get("api")
    if api != 1:
        raise ValueError("extension.api must be 1")
    min_rozi = optional_string(extension, "min_rozi")
    if min_rozi and not SEMVER_PATTERN.fullmatch(min_rozi):
        raise ValueError("extension.min_rozi must be a semantic version")
    platforms = extension.get("platforms", [])
    if not isinstance(platforms, list) or any(not isinstance(item, str) for item in platforms):
        raise ValueError("extension.platforms must be an array of strings")
    if len(set(platforms)) != len(platforms) or not set(platforms).issubset(ALLOWED_PLATFORMS):
        raise ValueError("extension.platforms contains duplicates or unsupported platforms")
    homepage = optional_string(extension, "homepage")
    if homepage and urllib.parse.urlparse(homepage).scheme not in {"http", "https"}:
        raise ValueError("extension.homepage must be an HTTP(S) URL")

    full_name = required_string(repo, "full_name")
    source = required_string(repo, "clone_url")
    if source != f"https://github.com/{full_name}.git":
        raise ValueError("repository has a non-canonical HTTPS clone URL")
    updated_at = required_string(repo, "updated_at")
    stars = repo.get("stargazers_count")
    if not isinstance(stars, int) or stars < 0:
        raise ValueError("repository star count is invalid")

    return {
        "repository": full_name,
        "source": source,
        "commit": commit,
        "manifest_path": "extension.toml",
        "id": extension_id,
        "title": required_string(extension, "title"),
        "description": required_string(extension, "description"),
        "version": required_string(extension, "version"),
        "api": api,
        "min_rozi": min_rozi,
        "platforms": platforms,
        "homepage": homepage,
        "stars": stars,
        "updated_at": updated_at,
        "commands": contribution_count(manifest, "commands"),
        "services": contribution_count(manifest, "services"),
        "agents": contribution_count(manifest, "agents"),
        "sidebar_tabs": contribution_count(manifest, "sidebar_tabs"),
        "navigation_targets": contribution_count(manifest, "navigation_targets"),
        "suggested_keybindings": contribution_count(manifest, "suggested_keybindings"),
    }


def build(client: GitHubClient, generated_at: datetime | None = None) -> tuple[dict[str, Any], list[str]]:
    entries: list[dict[str, Any]] = []
    skipped: list[str] = []
    for repo in repositories(client):
        full_name = repo.get("full_name", "<unknown>")
        if repo.get("private") or repo.get("archived") or repo.get("disabled") or repo.get("fork"):
            skipped.append(f"{full_name}: repository is private, archived, disabled, or a fork")
            continue
        try:
            branch = required_string(repo, "default_branch")
            commit_document = client.get(f"/repos/{full_name}/commits/{urllib.parse.quote(branch, safe='')}")
            commit = required_string(commit_document, "sha")
            manifest_document = client.get(
                f"/repos/{full_name}/contents/extension.toml", {"ref": commit}
            )
            if manifest_document.get("encoding") != "base64":
                raise ValueError("extension.toml was not returned as base64")
            content = manifest_document.get("content")
            if not isinstance(content, str):
                raise ValueError("extension.toml has no content")
            encoded = "".join(content.split())
            entries.append(catalog_entry(repo, commit, base64.b64decode(encoded, validate=True)))
        except (
            binascii.Error,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            tomllib.TOMLDecodeError,
            RuntimeError,
        ) as error:
            skipped.append(f"{full_name}: {error}")

    entries.sort(key=lambda entry: (entry["title"].casefold(), entry["repository"].casefold()))
    timestamp = generated_at or datetime.now(timezone.utc)
    return {
        "schema_version": 1,
        "generated_at": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "extensions": entries,
    }, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document, skipped = build(GitHubClient(os.environ.get("GITHUB_TOKEN")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    for message in skipped:
        print(f"skipped: {message}", file=sys.stderr)
    print(f"wrote {len(document['extensions'])} extensions to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
