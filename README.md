# Rozi extension index

This repository publishes the public discovery index consumed by Rozi's Extensions picker.
Repositories opt in by adding the `rozi-extension` GitHub topic and placing `extension.toml` at the
repository root.

The index is generated from public GitHub metadata and manifests. It is not a package repository,
security audit, endorsement, or review service. Extensions remain executable software installed
from their original repositories. Rozi runs an extension as the user who installed it, with no
sandbox, so it can read and change anything that account can. Read an extension's source before
installing it.

## Endpoints

- Index: `https://tui-lipan.github.io/rozi-extension-index/v1/index.json`
- Schema: `https://tui-lipan.github.io/rozi-extension-index/v1/schema.json`
- Repositories left out, and why: `https://tui-lipan.github.io/rozi-extension-index/v1/skipped.json`
- Its schema: `https://tui-lipan.github.io/rozi-extension-index/v1/skipped.schema.json`

## Inclusion

An entry must be a public, non-archived, non-fork repository with:

- the `rozi-extension` topic;
- a root `extension.toml` on its default branch;
- extension API 1;
- catalog metadata: `id`, `title`, `description`, and `version`.

Metadata is bounded so one repository cannot crowd out the rest: `id`, `version`, and `min_rozi`
up to 64 characters, `title` up to 80, `description` up to 280, and `homepage` up to 256. A
repository over a limit is skipped. The published index never exceeds 1 MiB, the most Rozi reads;
if it would, the entries with the fewest stars, then the least recently updated, are left out.

The generator reads metadata at an exact commit. It never executes repository code or initializes
submodules. Rozi validates the complete extension again before installation.

## Why is my extension missing?

The index is rebuilt daily, so a new or changed repository appears after the next build. Every
public repository with the topic that the build left out is listed in `v1/skipped.json` with the
reason, such as a missing `description` or a field over its limit. Fix the manifest on the default
branch and wait for the next build. `rozi extensions check .` in the checkout lists the same
problems under **Discover** before you publish.

## Development

```sh
python -m unittest discover -s tests
GITHUB_TOKEN=... python scripts/build_index.py --output public/v1/index.json
```

GitHub Actions rebuilds and deploys the index daily and on demand.

## License

MPL-2.0.
