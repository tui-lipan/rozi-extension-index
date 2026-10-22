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
# Semantic Versioning 2.0.0, as Rozi's `semver` parser reads it: no empty identifiers and no
# leading zeros in numeric pre-release identifiers.
SEMVER_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
HOST_PATTERN = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
# Longest skip reason published, in characters. A GitHub error can quote a whole response body.
MAX_REASON_CHARS = 300
# Rozi refuses an index larger than this, which would take discovery offline for every client.
# Keep it equal to `MAX_INDEX_BYTES` in Rozi's `src/extension_catalog.rs`.
MAX_INDEX_BYTES = 1024 * 1024
# Longest accepted value, in characters, per metadata field. They bound each record so one
# repository cannot crowd the rest out of the index. Keep them equal to `FIELD_LIMITS` in Rozi's
# `src/extension_catalog.rs`.
MAX_FIELD_CHARS = {
    "id": 64,
    "title": 80,
    "description": 280,
    "version": 64,
    "min_rozi": 64,
    "homepage": 256,
}


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
    return bounded(key, value.strip())


def optional_string(table: dict[str, Any], key: str) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"extension.{key} must be a non-empty string when present")
    return bounded(key, value.strip())


def bounded(key: str, value: str) -> str:
    limit = MAX_FIELD_CHARS.get(key)
    if limit is not None and len(value) > limit:
        raise ValueError(f"extension.{key} is longer than {limit} characters")
    return value


def contribution_count(manifest: dict[str, Any], key: str) -> int:
    value = manifest.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{key} must be an array")
    return len(value)


def valid_homepage(homepage: str) -> bool:
    """Accept only what Rozi accepts: an HTTP(S) URL with a real host.

    Rozi rejects a record whose homepage it cannot parse, so anything doubtful is refused here,
    where the repository is skipped with a reason, rather than shipped to every client.
    """
    if any(character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F for character in homepage):
        return False
    try:
        parsed = urllib.parse.urlsplit(homepage)
        parsed.port
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or "@" in parsed.netloc:
        return False
    return bool(parsed.hostname) and HOST_PATTERN.fullmatch(parsed.hostname) is not None


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
    # `True == 1` in Python, but Rozi reads the field as an integer.
    if type(api) is not int or api != 1:
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
    if homepage and not valid_homepage(homepage):
        raise ValueError("extension.homepage must be an HTTP(S) URL with a host")

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


def build(
    client: GitHubClient, generated_at: datetime | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The index, and the report of every topic repository it left out and why."""
    entries: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    for repo in repositories(client):
        full_name = repo.get("full_name", "<unknown>")
        # The report is public: a private repository the token can see is left out unnamed.
        if repo.get("private") is not False:
            continue
        excluded = [
            description
            for state, description in (("archived", "archived"), ("disabled", "disabled"), ("fork", "a fork"))
            if repo.get(state)
        ]
        if excluded:
            skipped.append((full_name, f"repository is {' and '.join(excluded)}"))
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
            skipped.append((full_name, str(error)))

    timestamp = generated_at or datetime.now(timezone.utc)
    generated = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    document = {"schema_version": 1, "generated_at": generated, "extensions": entries}
    skipped.extend(fit_size_limit(document))
    entries.sort(key=lambda entry: (entry["title"].casefold(), entry["repository"].casefold()))
    report = {
        "schema_version": 1,
        "generated_at": generated,
        "skipped": [
            {"repository": repository, "reason": short_reason(reason)}
            for repository, reason in sorted(skipped, key=lambda item: item[0].casefold())
        ],
    }
    return document, report


def short_reason(reason: str) -> str:
    """One line, bounded, so an error that quotes a response body stays readable."""
    line = " ".join(reason.split())
    return line if len(line) <= MAX_REASON_CHARS else line[: MAX_REASON_CHARS - 1] + "…"


def serialize(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")


def fit_size_limit(document: dict[str, Any]) -> list[tuple[str, str]]:
    """Drop the least established entries until the index fits the size Rozi accepts.

    Rozi refuses an oversized index whole, so shedding the entries with the fewest stars, then the
    least recently updated, keeps discovery working for everything else.
    """
    entries = document["extensions"]
    entries.sort(key=lambda entry: (entry["stars"], entry["updated_at"]), reverse=True)
    dropped = []
    while entries and len(serialize(document)) > MAX_INDEX_BYTES:
        entry = entries.pop()
        dropped.append((entry["repository"], f"index would exceed {MAX_INDEX_BYTES} bytes"))
    return dropped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--skipped-output", type=Path, help="also write the report of repositories left out"
    )
    args = parser.parse_args()
    document, report = build(GitHubClient(os.environ.get("GITHUB_TOKEN")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(serialize(document))
    if args.skipped_output:
        args.skipped_output.parent.mkdir(parents=True, exist_ok=True)
        args.skipped_output.write_bytes(serialize(report))
    for item in report["skipped"]:
        print(f"skipped: {item['repository']}: {item['reason']}", file=sys.stderr)
    print(f"wrote {len(document['extensions'])} extensions to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
