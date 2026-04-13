# Design: Allow Empty Config for Full Deletion

**Date:** 2026-04-13  
**Status:** Approved

## Context

When decommissioning a Dataform repository, operators need to delete all release
and workflow configurations. The current JSON schema requires at least one
`release_configs` entry (`minItems: 1`), making a "delete everything" state
inexpressible in the config file.

Additionally, the current sync-delete order is incorrect: orphaned release
configs are deleted in Step 1, before orphaned workflow configs are deleted in
Step 3. Because workflow configs reference release configs, attempting to delete
a release config that is still referenced by a workflow config will fail at the
API level.

## Goals

1. Allow `release_configs: []` to express "no configurations desired".
2. Require an explicit `allow_empty_config: true` Action input to guard against
   accidental full deletion.
3. Fix the sync-delete ordering so workflow configs are always deleted before
   release configs.

## Non-Goals

- Supporting per-config deletion via the JSON (use `sync_delete` with a reduced
  list instead).
- Adding a separate "destroy mode" that bypasses the JSON entirely.

## Design

### Schema change (`schema.json`)

Remove `minItems: 1` from `release_configs` to allow an empty array.

```json
"release_configs": {
  "type": "array",
  "items": { ... }
}
```

`workflow_configs` already has no `minItems` constraint — no change needed.

### Action input (`action.yml`)

Add an `allow_empty_config` input that maps to the `ALLOW_EMPTY_CONFIG`
environment variable (default: `false`).

```yaml
allow_empty_config:
  description: >
    Allow release_configs to be empty. Required when intentionally deleting all
    configurations. Combine with sync_delete: true to remove everything from GCP.
  required: false
  default: 'false'
```

### Validation in `apply.py`

In `main()`, after loading the config:

```
if release_configs is empty and ALLOW_EMPTY_CONFIG != true:
    print error: "release_configs is empty. Set allow_empty_config: true if you
                  intend to delete all configurations."
    sys.exit(1)
```

### Sync-delete ordering fix (`apply.py`)

Current (broken) order:

| Step | Action |
|------|--------|
| 1 | Upsert release configs |
| 1 | Sync-delete orphaned release configs ← too early |
| 2 | Compile |
| 3 | Upsert workflow configs |
| 3 | Sync-delete orphaned workflow configs |

Fixed order:

| Step | Action |
|------|--------|
| 1 | Upsert release configs only (no deletion) |
| 2 | Compile |
| 3 | Upsert workflow configs |
| 3 | Sync-delete orphaned workflow configs |
| 3 | Sync-delete orphaned release configs |

Implementation: move the sync-delete block out of `deploy_release_configs` and
append it at the end of `deploy_workflow_configs`, after workflow sync-delete
completes.

`deploy_release_configs` will no longer accept or use `sync_delete`. The
`sync_delete` parameter is passed only to `deploy_workflow_configs`, which now
handles cleanup for both resource types.

The `release_configs_deleted` and `release_configs_delete_failed` outputs are
set inside `deploy_workflow_configs` after the workflow cleanup.

### Full deletion workflow (example)

```json
{
  "$schema": "...",
  "repository": "my-repo",
  "release_configs": []
}
```

```yaml
- uses: snhryt-neo/apply-dataform-workflows@v1
  with:
    config_file: delete_all.json
    sync_delete: true
    allow_empty_config: true
```

Runtime behaviour:
1. `release_configs` is empty + `allow_empty_config=true` → validation passes.
2. Step 1: no upserts (empty list), no deletions.
3. Step 2: compile skipped (nothing to compile).
4. Step 3: no upserts; sync-delete removes all workflow configs from GCP, then
   all release configs from GCP.

## Error Handling

| Condition | Behaviour |
|-----------|-----------|
| `release_configs: []` without `allow_empty_config: true` | Error exit with clear message |
| API error during workflow config delete | Logged, recorded as failed, continue to next |
| API error during release config delete | Logged, recorded as failed, continue to next |
| API error listing resources for sync-delete | Warning logged, sync-delete skipped |

## Testing

- Unit test: empty `release_configs` without flag → exits with error message.
- Unit test: empty `release_configs` with flag → proceeds normally.
- Unit test: `deploy_workflow_configs` with sync-delete calls release config
  deletion after workflow config deletion (verify call order with mocks).
- Unit test: existing test for sync-delete of release configs moves to the
  workflow config test file.
