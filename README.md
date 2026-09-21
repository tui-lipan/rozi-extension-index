# Rozi extension index

This repository publishes the public discovery index consumed by Rozi's Extensions picker.
Repositories opt in by adding the `rozi-extension` GitHub topic and placing `extension.toml` at the
repository root.

The index is generated from public GitHub metadata and manifests. It is not a package repository,
security audit, endorsement, or review service. Extensions remain executable software installed
from their original repositories.

## Endpoints

- Index: `https://tui-lipan.github.io/rozi-extension-index/v1/index.json`
- Schema: `https://tui-lipan.github.io/rozi-extension-index/v1/schema.json`

## Inclusion

An entry must be a public, non-archived, non-fork repository with:

- the `rozi-extension` topic;
- a root `extension.toml` on its default branch;
- extension API 1;
- catalog metadata: `id`, `title`, `description`, and `version`.

The generator reads metadata at an exact commit. It never executes repository code or initializes
submodules. Rozi validates the complete extension again before installation.

## Development

```sh
python -m unittest discover -s tests
GITHUB_TOKEN=... python scripts/build_index.py --output public/v1/index.json
```

GitHub Actions rebuilds and deploys the index daily and on demand.

## License

MPL-2.0.
