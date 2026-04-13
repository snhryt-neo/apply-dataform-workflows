# CLAUDE.md

## Project overview

`apply-dataform-workflows` is a GitHub Composite Action that applies Dataform release configurations, workflow configurations, and compilation overrides from a single JSON SSoT file via the Dataform REST API.

## Architecture

```
action.yml          → Composite Action definition (inputs/outputs, calls apply via uv run)
src/apply_dataform_workflows/
  client.py         → DataformApiClient (AuthorizedSession + REST API)
  config.py         → ConfigLoader + dataclasses for JSON config
  apply.py          → Entry point, 3-step apply flow, GitHub outputs
schema.json         → JSON Schema for the SSoT config file
examples/           → Sample config JSON files
tests/              → pytest tests (test_client.py, test_config.py, test_apply.py)
```

### Processing flow (apply.py)

1. Upsert release configurations (GET → PATCH or POST, but use delete → recreate when `gitCommitish` or `codeCompilationConfig` changes on an existing resource)
2. POST compilationResults + PATCH releaseConfig (if `DO_COMPILE=true`)
3. Upsert workflow configurations (GET → PATCH or POST, but use delete → recreate when `invocationConfig` changes on an existing resource)

Release configs must exist before workflow configs reference them.

**Ordering invariant**: creation/upsert is always release configs → workflow configs. Deletion (sync-delete) is the reverse: workflow configs → release configs. This prevents API errors from deleting a release config that a workflow config still references.

## Key design decisions

- **Action input → env var naming**: `compile` → `DO_COMPILE`, `workflow_settings_file` → `WORKFLOW_SETTINGS`. Match these when testing locally.
- **snake_case config → camelCase API**: `ConfigLoader` converts fields; alias table for `cron`, `options`, `tags`, `full_refresh`. `id` is stripped before API calls.
- **workflow_settings.yaml driven**: `project_id`/`location` from `defaultProject`/`defaultLocation`. Explicit inputs override.
- **Auth**: ADC via `google-auth` + `AuthorizedSession`. Assumes `GOOGLE_APPLICATION_CREDENTIALS` is set by `google-github-actions/auth`.
- **Sync-delete**: When `sync_delete` is true, configs not in JSON are deleted from Google Cloud.
- **Immutable-update handling**: `apply.py` compares the current API resource before updating. For release configs, changes to `gitCommitish` or `codeCompilationConfig` use delete + recreate; for workflow configs, changes to `invocationConfig` use delete + recreate. This keeps the JSON config authoritative without relying on a later PATCH error from the API server.

## Linting & testing

```bash
uv run ruff check . && uv run ruff format .  # lint (also runs via pre-commit)
uv run pytest
```

## Local testing

```bash
# Dry-run
CONFIG_FILE=examples/release_workflow_config_simple.json WORKFLOW_SETTINGS=examples/workflow_settings.yaml DO_COMPILE=false DRY_RUN=true uv run python -m apply_dataform_workflows.apply

# Real settings (tests/release_workflow_config.json and tests/workflow_settings.yaml are git ignored)
CONFIG_FILE=tests/release_workflow_config.json WORKFLOW_SETTINGS=tests/workflow_settings.yaml DO_COMPILE=false uv run python -m apply_dataform_workflows.apply
```

> Set `DO_COMPILE=false` — otherwise it compiles against this repo, not your Dataform repository.

## Language & conventions

- All docs, comments, and commits MUST be in English. Exception: `README-ja.md`.
- Python: `src/` layout via uv. Formatting via ruff.

## Git conventions

- GitHub Flow: branch from `main`, PR, merge to `main`. **Never push directly to `main`**.
- Branch naming: `<type>/<short-description>` (e.g., `feat/add-dry-run`, `fix/upsert-error`)
- Commit/PR titles: [Conventional Commits](https://www.conventionalcommits.org/) — `<type>: <description>`

## CI/CD

- **CI** (`.github/workflows/ci.yml`): ruff + pytest on push/PR to main.
- **Release** (`.github/workflows/release.yml`): `v*.*.*` tag → GitHub Release + floating major tag (`v1.2.3` → `v1`).
