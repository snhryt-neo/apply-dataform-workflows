# Allow Empty Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow `release_configs: []` in the JSON config (with an explicit `allow_empty_config: true` Action input) to support full deletion of all Dataform configs on repository decommission, while also fixing an existing sync-delete ordering bug where release configs were deleted before workflow configs.

**Architecture:** Two changes in one PR: (1) move release-config sync-delete to after workflow-config sync-delete inside `deploy_workflow_configs`; (2) remove the empty-array guard from `ConfigLoader` and add it back as an env-var-gated check in `main()`.

**Tech Stack:** Python 3.x, pytest, uv, GitHub Composite Action YAML

---

## File Map

| File | Change |
|------|--------|
| `src/apply_dataform_workflows/apply.py` | Remove `sync_delete` param from `deploy_release_configs`; move release sync-delete into `deploy_workflow_configs` after workflow sync-delete; add `ALLOW_EMPTY_CONFIG` validation in `main()` |
| `src/apply_dataform_workflows/config.py` | Remove `if not release_configs: raise ValueError(...)` (lines 108-109) |
| `schema.json` | Remove `minItems: 1` from `release_configs` |
| `action.yml` | Add `allow_empty_config` input + `ALLOW_EMPTY_CONFIG` env var |
| `tests/test_apply.py` | Update call signatures; migrate release sync-delete tests to `TestDeployWorkflowConfigs`; add ordering test; add `allow_empty_config` tests in `TestMain` |
| `tests/test_config.py` | Remove `test_empty_release_configs_raises` |
| `README.md` | Add `allow_empty_config` to Inputs table |
| `README-ja.md` | Same in Japanese |

---

### Task 1: Fix sync-delete ordering — update tests first

**Files:**
- Modify: `tests/test_apply.py`

- [ ] **Step 1: Remove sync_delete arg from all `deploy_release_configs` calls in `TestDeployReleaseConfigs`**

The new signature will be `deploy_release_configs(client, config, output)` (no `sync_delete`).
Change every call in `TestDeployReleaseConfigs` from `deploy_release_configs(mock_client, config, False, github_output)` → `deploy_release_configs(mock_client, config, github_output)` and `deploy_release_configs(mock_client, config, True, ...)` → see step 2.

There are 14 call sites total in that class. The `sync_delete=True` calls are in:
- `test_sync_delete_removes_orphans`
- `test_sync_delete_preserves_listed_configs`
- `test_sync_delete_warns_on_list_failure`

All others pass `False` as third arg — just drop the `False`.

- [ ] **Step 2: Delete the three release sync-delete tests from `TestDeployReleaseConfigs`**

Remove these entire test methods from `TestDeployReleaseConfigs`:
- `test_sync_delete_removes_orphans`
- `test_sync_delete_preserves_listed_configs`
- `test_sync_delete_warns_on_list_failure`

They will be re-added to `TestDeployWorkflowConfigs` in step 3.

- [ ] **Step 3: Update existing workflow sync-delete tests to also mock the release config list**

After the change, when `sync_delete=True`, `deploy_workflow_configs` will call `client.get("/releaseConfigs")` after the workflow list. Add a third mock entry to each test that uses `sync_delete=True`:

`test_sync_delete_removes_orphans` in `TestDeployWorkflowConfigs` — change `side_effect` from:
```python
mock_client.get.side_effect = [
    _json_response({
        "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
        "cronSchedule": "0 3 * * *",
        "timeZone": "Asia/Tokyo",
        "invocationConfig": {},
        "disabled": False,
    }),
    _json_response({
        "workflowConfigs": [
            {"name": f"{mock_client.parent}/workflowConfigs/daily-run"},
            {"name": f"{mock_client.parent}/workflowConfigs/old-workflow"},
        ]
    }),
]
```
to:
```python
mock_client.get.side_effect = [
    _json_response({
        "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
        "cronSchedule": "0 3 * * *",
        "timeZone": "Asia/Tokyo",
        "invocationConfig": {},
        "disabled": False,
    }),
    _json_response({
        "workflowConfigs": [
            {"name": f"{mock_client.parent}/workflowConfigs/daily-run"},
            {"name": f"{mock_client.parent}/workflowConfigs/old-workflow"},
        ]
    }),
    _json_response({"releaseConfigs": []}),
]
```

`test_sync_delete_preserves_listed_configs` — add `_json_response({"releaseConfigs": []})` as the third element.

`test_sync_delete_warns_on_list_failure` — add `_json_response({"releaseConfigs": []})` as the third element:
```python
mock_client.get.side_effect = [
    _json_response({
        "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
        "cronSchedule": "0 3 * * *",
        "timeZone": "Asia/Tokyo",
        "invocationConfig": {},
        "disabled": False,
    }),
    ApiError(500, "List failed"),
    _json_response({"releaseConfigs": []}),
]
```

- [ ] **Step 4: Add new tests to `TestDeployWorkflowConfigs` for release sync-delete**

Add after `test_sync_delete_warns_on_list_failure`:

```python
def test_sync_delete_removes_orphaned_release_configs(
    self, mock_client, github_output, fixtures_dir
):
    from apply_dataform_workflows.apply import deploy_workflow_configs

    config = ConfigLoader.load(fixtures_dir / "config_simple.json")
    mock_client.get.side_effect = [
        _json_response({
            "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
            "cronSchedule": "0 3 * * *",
            "timeZone": "Asia/Tokyo",
            "invocationConfig": {},
            "disabled": False,
        }),
        _json_response({
            "workflowConfigs": [
                {"name": f"{mock_client.parent}/workflowConfigs/daily-run"},
            ]
        }),
        _json_response({
            "releaseConfigs": [
                {"name": f"{mock_client.parent}/releaseConfigs/production"},
                {"name": f"{mock_client.parent}/releaseConfigs/old-release"},
            ]
        }),
    ]

    deploy_workflow_configs(mock_client, config, True, github_output)

    mock_client.delete.assert_called_once_with("/releaseConfigs/old-release")
    assert any(
        r.status == "deleted" and "releaseConfig" in r.resource
        for r in github_output.results
    )

def test_sync_delete_deletes_workflow_configs_before_release_configs(
    self, mock_client, github_output, fixtures_dir
):
    """Workflow configs must be deleted before release configs."""
    from apply_dataform_workflows.apply import deploy_workflow_configs

    config = ConfigLoader.load(fixtures_dir / "config_simple.json")
    mock_client.get.side_effect = [
        _json_response({
            "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
            "cronSchedule": "0 3 * * *",
            "timeZone": "Asia/Tokyo",
            "invocationConfig": {},
            "disabled": False,
        }),
        _json_response({
            "workflowConfigs": [
                {"name": f"{mock_client.parent}/workflowConfigs/daily-run"},
                {"name": f"{mock_client.parent}/workflowConfigs/old-workflow"},
            ]
        }),
        _json_response({
            "releaseConfigs": [
                {"name": f"{mock_client.parent}/releaseConfigs/production"},
                {"name": f"{mock_client.parent}/releaseConfigs/old-release"},
            ]
        }),
    ]

    deploy_workflow_configs(mock_client, config, True, github_output)

    delete_calls = [str(c) for c in mock_client.delete.call_args_list]
    wc_idx = next(i for i, c in enumerate(delete_calls) if "workflowConfigs" in c)
    rc_idx = next(i for i, c in enumerate(delete_calls) if "releaseConfigs" in c)
    assert wc_idx < rc_idx, "workflowConfig must be deleted before releaseConfig"

def test_sync_delete_sets_release_configs_deleted_output(
    self, mock_client, fixtures_dir, tmp_path
):
    from apply_dataform_workflows.apply import deploy_workflow_configs

    output_file = tmp_path / "github_output"
    output_file.write_text("")
    output = GitHubOutput(
        output_path=str(output_file),
        summary_path=str(tmp_path / "summary"),
    )
    config = ConfigLoader.load(fixtures_dir / "config_simple.json")
    mock_client.get.side_effect = [
        _json_response({
            "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
            "cronSchedule": "0 3 * * *",
            "timeZone": "Asia/Tokyo",
            "invocationConfig": {},
            "disabled": False,
        }),
        _json_response({"workflowConfigs": [
            {"name": f"{mock_client.parent}/workflowConfigs/daily-run"},
        ]}),
        _json_response({"releaseConfigs": [
            {"name": f"{mock_client.parent}/releaseConfigs/production"},
            {"name": f"{mock_client.parent}/releaseConfigs/old-release"},
        ]}),
    ]

    deploy_workflow_configs(mock_client, config, True, output)

    content = output_file.read_text()
    assert "release_configs_deleted=old-release\n" in content
    assert "release_configs_delete_failed=\n" in content
```

- [ ] **Step 5: Run the tests to verify they fail**

```bash
cd /Users/snhryt/workspace/apply-dataform-workflows
uv run pytest tests/test_apply.py -v 2>&1 | head -60
```

Expected: multiple failures about wrong number of arguments to `deploy_release_configs`, and about missing release sync-delete behavior.

---

### Task 2: Implement sync-delete ordering fix in `apply.py`

**Files:**
- Modify: `src/apply_dataform_workflows/apply.py`

- [ ] **Step 1: Remove `sync_delete` from `deploy_release_configs`**

Change the function signature at line 116 from:
```python
def deploy_release_configs(
    client: DataformApiClient,
    config: DeployConfig,
    sync_delete: bool,
    output: GitHubOutput,
) -> None:
```
to:
```python
def deploy_release_configs(
    client: DataformApiClient,
    config: DeployConfig,
    output: GitHubOutput,
) -> None:
```

- [ ] **Step 2: Remove the sync-delete block from `deploy_release_configs`**

Delete lines 216–266 (the entire `# Sync-delete orphaned release configs` block including the `deleted = []`, `delete_failed = []` declarations and the `if sync_delete` / `elif sync_delete` blocks).

Also update the output lines at the end to remove `release_configs_deleted` and `release_configs_delete_failed`. The final output block becomes:

```python
    output.set_output("release_configs_created", ",".join(created))
    output.set_output("release_configs_updated", ",".join(updated))
    output.set_output("release_configs_failed", ",".join(failed))
    print("::endgroup::")
```

- [ ] **Step 3: Update the early-return path in `deploy_workflow_configs`**

When `not config.workflow_configs and not sync_delete`, the function returns early. After moving release sync-delete into this function, this path must also set the release outputs. Find the early return block (around line 332) and add two lines:

```python
    if not config.workflow_configs and not sync_delete:
        print("  (skipped — no workflow_configs in config)")
        output.set_output("workflow_configs_created", "")
        output.set_output("workflow_configs_updated", "")
        output.set_output("workflow_configs_failed", "")
        output.set_output("workflow_configs_deleted", "")
        output.set_output("workflow_configs_delete_failed", "")
        output.set_output("release_configs_deleted", "")      # add this line
        output.set_output("release_configs_delete_failed", "") # add this line
        output.add_result(
            StepResult("3/3", "Workflow configurations", "skipped", "Skipped")
        )
        print("::endgroup::")
        return
```

- [ ] **Step 4: Add release sync-delete to `deploy_workflow_configs`**

After the existing `# Sync-delete orphaned workflow configs` block (and its `elif sync_delete and client.dry_run` branch), add the release config sync-delete and update the output block. Replace everything from the existing output.set_output lines at the end of the function with:

```python
    # Sync-delete orphaned release configs (after workflow configs to avoid reference errors)
    rc_deleted = []
    rc_delete_failed = []

    if sync_delete and not client.dry_run:
        desired_rc_ids = {rc.id for rc in config.release_configs}
        try:
            response = client.get("/releaseConfigs")
            existing_rcs = response.json().get("releaseConfigs", [])
            for entry in existing_rcs:
                existing_id = entry["name"].split("/")[-1]
                if existing_id not in desired_rc_ids:
                    try:
                        print(f"  Deleting releaseConfig: {existing_id}")
                        client.delete(f"/releaseConfigs/{existing_id}")
                        print(f"  Deleted releaseConfig: {existing_id}")
                        rc_deleted.append(existing_id)
                        output.add_result(
                            StepResult(
                                "3/3",
                                f"releaseConfig: {existing_id}",
                                "deleted",
                                "Deleted",
                            )
                        )
                    except ApiError as e:
                        print(
                            f"::error::Failed to delete releaseConfig"
                            f" '{existing_id}': {e.message}"
                        )
                        rc_delete_failed.append(existing_id)
                        output.add_result(
                            StepResult(
                                "3/3",
                                f"releaseConfig: {existing_id}",
                                "failed",
                                "Delete failed",
                            )
                        )
        except ApiError as e:
            print(
                f"::warning::Failed to list release configs for sync-delete: {e.message}"
            )
    elif sync_delete and client.dry_run:
        print("  [dry-run] Would check for orphaned release configs to delete")

    output.set_output("workflow_configs_created", ",".join(created))
    output.set_output("workflow_configs_updated", ",".join(updated))
    output.set_output("workflow_configs_failed", ",".join(failed))
    output.set_output("workflow_configs_deleted", ",".join(deleted))
    output.set_output("workflow_configs_delete_failed", ",".join(delete_failed))
    output.set_output("release_configs_deleted", ",".join(rc_deleted))
    output.set_output("release_configs_delete_failed", ",".join(rc_delete_failed))
    print("::endgroup::")
```

- [ ] **Step 5: Update the call site in `main()`**

Change line 563 from:
```python
    deploy_release_configs(client, config, sync_delete, output)
```
to:
```python
    deploy_release_configs(client, config, output)
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_apply.py -v 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/apply_dataform_workflows/apply.py tests/test_apply.py
git commit -m "fix: delete workflow configs before release configs during sync-delete"
```

---

### Task 3: Remove empty-array guard from config + schema

**Files:**
- Modify: `src/apply_dataform_workflows/config.py`
- Modify: `schema.json`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Remove `minItems: 1` from `schema.json`**

Change in `schema.json`:
```json
"release_configs": {
  "type": "array",
  "minItems": 1,
  "items": {
```
to:
```json
"release_configs": {
  "type": "array",
  "items": {
```

- [ ] **Step 2: Remove empty-array guard from `config.py`**

Delete lines 108–109 from `src/apply_dataform_workflows/config.py`:
```python
        if not release_configs:
            raise ValueError("release_configs must not be empty")
```

- [ ] **Step 3: Remove the now-invalid test from `test_config.py`**

Delete the `test_empty_release_configs_raises` test method (lines 635–639 in `tests/test_config.py`):
```python
    def test_empty_release_configs_raises(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text('{"repository": "repo", "release_configs": []}')
        with pytest.raises(ValueError, match="release_configs must not be empty"):
            ConfigLoader.load(bad_file)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_config.py -v 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add schema.json src/apply_dataform_workflows/config.py tests/test_config.py
git commit -m "fix: allow empty release_configs array in schema and config loader"
```

---

### Task 4: Add `allow_empty_config` validation

**Files:**
- Modify: `tests/test_apply.py`
- Modify: `action.yml`
- Modify: `src/apply_dataform_workflows/apply.py`

- [ ] **Step 1: Write failing tests in `TestMain`**

Add to `TestMain` in `tests/test_apply.py`:

```python
def test_empty_release_configs_without_flag_exits(self, monkeypatch, tmp_path):
    from apply_dataform_workflows.apply import main

    config_file = tmp_path / "config.json"
    config_file.write_text('{"repository": "repo", "release_configs": []}')
    env = self._env({
        "CONFIG_FILE": str(config_file),
        "ALLOW_EMPTY_CONFIG": "false",
    })
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(SystemExit):
        main()

def test_empty_release_configs_without_flag_prints_error(
    self, monkeypatch, tmp_path, capsys
):
    from apply_dataform_workflows.apply import main

    config_file = tmp_path / "config.json"
    config_file.write_text('{"repository": "repo", "release_configs": []}')
    env = self._env({
        "CONFIG_FILE": str(config_file),
        "ALLOW_EMPTY_CONFIG": "false",
    })
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(SystemExit):
        main()

    captured = capsys.readouterr()
    assert "allow_empty_config" in captured.out

@mock_patch("apply_dataform_workflows.apply.DataformApiClient")
def test_empty_release_configs_with_flag_proceeds(
    self, mock_client_cls, monkeypatch, tmp_path
):
    from apply_dataform_workflows.apply import main

    config_file = tmp_path / "config.json"
    config_file.write_text('{"repository": "repo", "release_configs": []}')
    env = self._env({
        "CONFIG_FILE": str(config_file),
        "ALLOW_EMPTY_CONFIG": "true",
        "DRY_RUN": "false",
        "SYNC_DELETE": "false",
    })
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    mock_client = MagicMock()
    mock_client.parent = (
        "projects/test-project/locations/asia-northeast1/repositories/repo"
    )
    mock_client.dry_run = False
    mock_client_cls.return_value = mock_client

    main()  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_apply.py::TestMain::test_empty_release_configs_without_flag_exits -v
uv run pytest tests/test_apply.py::TestMain::test_empty_release_configs_with_flag_proceeds -v
```

Expected: both FAIL (config load raises or validation not yet in place).

- [ ] **Step 3: Add `allow_empty_config` to `action.yml`**

Add after the `dry_run` input block (around line 35):

```yaml
  allow_empty_config:
    description: "Allow release_configs to be empty. Set to true only when intentionally deleting all configurations (combine with sync_delete: true)."
    required: false
    default: "false"
```

And add the env var to the deploy step env block:

```yaml
        ALLOW_EMPTY_CONFIG: ${{ inputs.allow_empty_config }}
```

- [ ] **Step 4: Add validation to `main()` in `apply.py`**

Add `allow_empty_config` env var read alongside the other optional vars (around line 501):

```python
    allow_empty_config = os.environ.get("ALLOW_EMPTY_CONFIG", "false").lower() == "true"
```

Then, after the `config = ConfigLoader.load(...)` call succeeds (around line 518, after the except block), add:

```python
    if not config.release_configs and not allow_empty_config:
        print(
            "::error::release_configs is empty."
            " Set allow_empty_config: true if you intend to delete all configurations."
        )
        sys.exit(1)
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_apply.py -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add action.yml src/apply_dataform_workflows/apply.py tests/test_apply.py
git commit -m "feat: add allow_empty_config input to enable full deletion via empty release_configs"
```

---

### Task 5: Update README.md and README-ja.md

**Files:**
- Modify: `README.md`
- Modify: `README-ja.md`

- [ ] **Step 1: Add `allow_empty_config` to inputs table in `README-ja.md`**

In `README-ja.md`, find the Inputs table (around line 122) and add a row for `allow_empty_config`:

```markdown
| `allow_empty_config` | `false` | `release_configs` を空にすることを許可する。リポジトリ廃止時に全設定を削除する場合に使用。`sync_delete: true` と組み合わせて使う |
```

- [ ] **Step 2: Sync README.md from README-ja.md**

Use the `sync-readme` skill to update `README.md` to match.

- [ ] **Step 3: Commit**

```bash
git add README.md README-ja.md
git commit -m "docs: document allow_empty_config input"
```

---

### Task 6: Delete spec file and clean up docs/

**Files:**
- Delete: `docs/superpowers/specs/2026-04-13-allow-empty-config-design.md`

- [ ] **Step 1: Delete the spec file and empty directories**

```bash
rm docs/superpowers/specs/2026-04-13-allow-empty-config-design.md
rmdir docs/superpowers/specs docs/superpowers docs 2>/dev/null || true
```

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "chore: remove design spec (implemented)"
```
