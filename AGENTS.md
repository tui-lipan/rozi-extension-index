# Rozi extension index agent guide

## Mission

Publish a deterministic, versioned index of public GitHub repositories that opt in with the
`rozi-extension` topic. The index is discovery metadata, not an audit or package repository.

## Commands

- Test: `python -m unittest discover -s tests`
- Generate: `python scripts/build_index.py --output public/v1/index.json`
- Whitespace: `git diff --check`

## Rules

- Use only the Python standard library.
- Never execute repository code, initialize submodules, or download release artifacts.
- Resolve and fetch `extension.toml` at one exact default-branch commit.
- Keep `public/v1/schema.json` and the generated payload contract synchronized.
- Keep validation, field limits, and the index size limit identical to Rozi's
  `src/extension_catalog.rs`. Rozi drops a record it cannot validate and refuses an oversized index.
- Generated `public/v1/index.json` is a deployment artifact and is not committed.
- Within `v1`, add only optional, informational fields, and leave a field out of a record rather
  than publish it empty: Rozi before 0.0.36 drops a record with any field it does not know. A field
  clients must understand for correctness or safety needs a new `schema_version`.
- Use Conventional Commits with a DCO sign-off.
