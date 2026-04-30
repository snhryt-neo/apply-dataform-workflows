from __future__ import annotations

from unittest.mock import call
from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch

import pytest

from apply_dataform_workflows.client import ApiError, UpsertResult
from apply_dataform_workflows.config import ConfigLoader
from apply_dataform_workflows.apply import GitHubOutput, StepResult


class TestStepResult:
    def test_creation(self):
        result = StepResult(
            step="1/3",
            resource="releaseConfig: production",
            status="success",
            detail="Created",
        )
        assert result.step == "1/3"
        assert result.status == "success"


class TestGitHubOutput:
    def test_set_output_writes_to_file(self, tmp_path):
        output_file = tmp_path / "github_output"
        output_file.write_text("")
        output = GitHubOutput(
            output_path=str(output_file),
            summary_path=str(tmp_path / "summary"),
        )

        output.set_output("key1", "value1")
        output.set_output("key2", "value2")

        content = output_file.read_text()
        assert "key1=value1\n" in content
        assert "key2=value2\n" in content

    def test_set_output_falls_back_to_devnull(self):
        output = GitHubOutput(output_path="/dev/null", summary_path="/dev/null")
        output.set_output("key", "value")

    def test_set_output_raises_on_newline_in_value(self):
        output = GitHubOutput(output_path="/dev/null", summary_path="/dev/null")
        with pytest.raises(ValueError, match="newlines"):
            output.set_output("key", "bad\nvalue")

    def test_set_output_raises_on_newline_in_key(self):
        output = GitHubOutput(output_path="/dev/null", summary_path="/dev/null")
        with pytest.raises(ValueError, match="'\\\\n' or '='"):
            output.set_output("bad\nkey", "value")

    def test_set_output_raises_on_equals_in_key(self):
        output = GitHubOutput(output_path="/dev/null", summary_path="/dev/null")
        with pytest.raises(ValueError, match="'\\\\n' or '='"):
            output.set_output("bad=key", "value")

    def test_write_summary(self, tmp_path):
        summary_file = tmp_path / "summary"
        summary_file.write_text("")
        output = GitHubOutput(
            output_path=str(tmp_path / "output"),
            summary_path=str(summary_file),
        )
        output.add_result(
            StepResult("1/3", "releaseConfig: prod", "success", "Updated")
        )
        output.add_result(StepResult("3/3", "workflowConfig: daily", "failed", "Error"))

        output.write_summary()

        content = summary_file.read_text()
        assert "Apply Dataform Release / Workflow Configurations" in content
        assert "prod" in content
        assert "daily" in content

    def test_has_failure_and_has_success(self):
        output = GitHubOutput(output_path="/dev/null", summary_path="/dev/null")
        output.add_result(StepResult("1/3", "a", "success", "OK"))
        output.add_result(StepResult("3/3", "b", "failed", "Error"))
        assert output.has_failure is True
        assert output.has_success is True

    def test_deployment_status_success(self):
        output = GitHubOutput(output_path="/dev/null", summary_path="/dev/null")
        output.add_result(StepResult("1/3", "a", "success", "OK"))
        assert output.deployment_status == "success"

    def test_deployment_status_failure(self):
        output = GitHubOutput(output_path="/dev/null", summary_path="/dev/null")
        output.add_result(StepResult("1/3", "a", "failed", "Error"))
        assert output.deployment_status == "failure"

    def test_deployment_status_partial_success(self):
        output = GitHubOutput(output_path="/dev/null", summary_path="/dev/null")
        output.add_result(StepResult("1/3", "a", "success", "OK"))
        output.add_result(StepResult("3/3", "b", "failed", "Error"))
        assert output.deployment_status == "partial_success"

    def test_from_env_reads_env_vars(self, tmp_path, monkeypatch):
        output_file = tmp_path / "output"
        summary_file = tmp_path / "summary"
        output_file.write_text("")
        summary_file.write_text("")
        monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

        output = GitHubOutput.from_env()
        output.set_output("test", "works")

        assert "test=works" in output_file.read_text()

    def test_from_env_defaults_to_devnull(self, monkeypatch):
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        output = GitHubOutput.from_env()
        output.set_output("test", "works")

    def test_deployment_status_with_no_results(self):
        output = GitHubOutput(output_path="/dev/null", summary_path="/dev/null")
        assert output.deployment_status == "success"


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.parent = (
        "projects/test-project/locations/asia-northeast1/repositories/test-repo"
    )
    client.dry_run = False
    return client


@pytest.fixture
def github_output():
    return GitHubOutput(output_path="/dev/null", summary_path="/dev/null")


def _json_response(body: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = body
    return response


class TestDeployReleaseConfigs:
    def test_deploys_single_release_config(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        # Use an outdated cronSchedule so the update path is exercised.
        mock_client.get.return_value = _json_response(
            {
                "gitCommitish": "main",
                "cronSchedule": "OLD",
                "timeZone": "Asia/Tokyo",
                "disabled": False,
            }
        )

        deploy_release_configs(mock_client, config, github_output)

        mock_client.patch.assert_called_once_with(
            "/releaseConfigs/production",
            {
                "gitCommitish": "main",
                "cronSchedule": "0 0 * * *",
                "timeZone": "Asia/Tokyo",
                "disabled": False,
            },
            params={"updateMask": "cronSchedule,timeZone,disabled"},
        )
        assert any(result.status == "success" for result in github_output.results)
        assert github_output.results[0].detail == "Updated"

    def test_deploys_multiple_release_configs(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_advanced.json")
        mock_client.get.side_effect = [
            ApiError(404, "Not found"),
            ApiError(404, "Not found"),
        ]

        deploy_release_configs(mock_client, config, github_output)

        assert mock_client.post.call_count == 2
        first_body = mock_client.post.call_args_list[0].args[1]
        second_body = mock_client.post.call_args_list[1].args[1]
        assert first_body == {
            "gitCommitish": "main",
            "cronSchedule": "0 0 * * *",
            "timeZone": "Asia/Tokyo",
            "disabled": False,
        }
        assert mock_client.post.call_args_list[0].kwargs["params"] == {
            "releaseConfigId": "production"
        }
        assert second_body == {
            "gitCommitish": "develop",
            "codeCompilationConfig": {
                "defaultDatabase": "my-project-dev",
                "schemaSuffix": "_dev",
                "vars": {"env": "development"},
            },
            "disabled": False,
        }
        assert mock_client.post.call_args_list[1].kwargs["params"] == {
            "releaseConfigId": "development"
        }
        assert [result.detail for result in github_output.results] == [
            "Created",
            "Created",
        ]

    def test_sets_release_config_created_output(
        self, mock_client, fixtures_dir, tmp_path
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        output_file = tmp_path / "github_output"
        output_file.write_text("")
        output = GitHubOutput(
            output_path=str(output_file),
            summary_path=str(tmp_path / "summary"),
        )
        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.get.side_effect = ApiError(404, "Not found")

        deploy_release_configs(mock_client, config, output)

        content = output_file.read_text()
        assert "release_configs_created=production\n" in content
        assert "release_configs_updated=\n" in content

    def test_sets_release_config_updated_output(
        self, mock_client, fixtures_dir, tmp_path
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        output_file = tmp_path / "github_output"
        output_file.write_text("")
        output = GitHubOutput(
            output_path=str(output_file),
            summary_path=str(tmp_path / "summary"),
        )
        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        # Use an outdated cronSchedule so the config is detected as changed.
        mock_client.get.return_value = _json_response(
            {
                "gitCommitish": "main",
                "cronSchedule": "OLD",
                "timeZone": "Asia/Tokyo",
                "disabled": False,
            }
        )

        deploy_release_configs(mock_client, config, output)

        content = output_file.read_text()
        assert "release_configs_created=\n" in content
        assert "release_configs_updated=production\n" in content

    def test_records_dry_run(self, mock_client, github_output, fixtures_dir):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.dry_run = True
        mock_client.upsert.return_value = UpsertResult.DRY_RUN

        deploy_release_configs(mock_client, config, github_output)

        assert github_output.results[0].status == "dry_run"

    def test_records_failure_on_api_error(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.get.side_effect = ApiError(500, "Server error")

        deploy_release_configs(mock_client, config, github_output)

        assert github_output.results[0].status == "failed"

    def test_recreates_release_config_when_git_commitish_changes(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.get.return_value = _json_response(
            {
                "gitCommitish": "old-main",
                "cronSchedule": "0 0 * * *",
                "timeZone": "Asia/Tokyo",
                "disabled": False,
            }
        )

        deploy_release_configs(mock_client, config, github_output)

        expected_body = {
            "gitCommitish": "main",
            "cronSchedule": "0 0 * * *",
            "timeZone": "Asia/Tokyo",
            "disabled": False,
        }
        assert mock_client.method_calls[:3] == [
            call.get("/releaseConfigs/production"),
            call.delete("/releaseConfigs/production"),
            call.post(
                "/releaseConfigs",
                expected_body,
                params={"releaseConfigId": "production"},
            ),
        ]
        assert github_output.results[0].status == "success"
        assert github_output.results[0].detail == "Recreated"

    def test_recreates_release_config_when_compile_override_changes(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_advanced.json")
        mock_client.get.side_effect = [
            _json_response(
                {
                    "gitCommitish": "main",
                    "cronSchedule": "0 0 * * *",
                    "timeZone": "Asia/Tokyo",
                    "disabled": False,
                }
            ),
            _json_response(
                {
                    "gitCommitish": "develop",
                    "codeCompilationConfig": {
                        "defaultDatabase": "different-project",
                        "schemaSuffix": "_dev",
                        "vars": {"env": "development"},
                    },
                    "disabled": False,
                }
            ),
        ]

        deploy_release_configs(mock_client, config, github_output)

        expected_body = {
            "gitCommitish": "develop",
            "codeCompilationConfig": {
                "defaultDatabase": "my-project-dev",
                "schemaSuffix": "_dev",
                "vars": {"env": "development"},
            },
            "disabled": False,
        }
        # production is up to date (no PATCH), so development is at index [1].
        assert mock_client.method_calls[1:4] == [
            call.get("/releaseConfigs/development"),
            call.delete("/releaseConfigs/development"),
            call.post(
                "/releaseConfigs",
                expected_body,
                params={"releaseConfigId": "development"},
            ),
        ]
        assert github_output.results[1].status == "success"
        assert github_output.results[1].detail == "Recreated"

    def test_records_failure_when_release_recreate_fails(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.get.return_value = _json_response(
            {
                "gitCommitish": "old-main",
                "cronSchedule": "0 0 * * *",
                "timeZone": "Asia/Tokyo",
                "disabled": False,
            }
        )
        mock_client.post.side_effect = ApiError(500, "Recreate failed")

        deploy_release_configs(mock_client, config, github_output)

        assert mock_client.delete.called
        assert mock_client.post.called
        assert github_output.results[0].status == "failed"

    def test_strips_id_from_body(self, mock_client, github_output, fixtures_dir):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        # Use an outdated cronSchedule to trigger PATCH so the body can be inspected.
        mock_client.get.return_value = _json_response(
            {
                "gitCommitish": "main",
                "cronSchedule": "OLD",
                "timeZone": "Asia/Tokyo",
                "disabled": False,
            }
        )

        deploy_release_configs(mock_client, config, github_output)

        body = mock_client.patch.call_args.args[1]
        assert "id" not in body

    def test_release_config_disabled_true_sets_disabled_and_update_mask(
        self, mock_client, github_output, tmp_path
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config_file = tmp_path / "config.json"
        config_file.write_text(
            """
            {
              "repository": "repo",
              "release_configs": [
                {
                  "id": "production",
                  "git_ref": "main",
                  "disabled": true
                }
              ]
            }
            """
        )
        config = ConfigLoader.load(config_file)
        # existing has disabled=False so the change to True triggers a PATCH.
        mock_client.get.return_value = _json_response(
            {
                "gitCommitish": "main",
                "disabled": False,
            }
        )

        deploy_release_configs(mock_client, config, github_output)

        assert mock_client.patch.call_args.args[1] == {
            "gitCommitish": "main",
            "disabled": True,
        }
        assert mock_client.patch.call_args.kwargs["params"] == {
            "updateMask": "disabled"
        }

    def test_release_config_without_disabled_defaults_disabled_and_update_mask(
        self, mock_client, github_output, tmp_path
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config_file = tmp_path / "config.json"
        config_file.write_text(
            """
            {
              "repository": "repo",
              "release_configs": [
                {
                  "id": "production",
                  "git_ref": "main"
                }
              ]
            }
            """
        )
        config = ConfigLoader.load(config_file)
        # existing has disabled=True so the change to False (default) triggers a PATCH.
        mock_client.get.return_value = _json_response(
            {
                "gitCommitish": "main",
                "disabled": True,
            }
        )

        deploy_release_configs(mock_client, config, github_output)

        assert mock_client.patch.call_args.args[1] == {
            "gitCommitish": "main",
            "disabled": False,
        }
        assert mock_client.patch.call_args.kwargs["params"] == {
            "updateMask": "disabled"
        }


class TestCompileReleaseConfigs:
    def test_skips_when_compile_false(self, mock_client, github_output, fixtures_dir):
        from apply_dataform_workflows.apply import compile_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        compile_release_configs(mock_client, config, False, github_output)

        mock_client.post.assert_not_called()
        assert github_output.results[0].status == "skipped"

    def test_dry_run_logs_intention(self, mock_client, github_output, fixtures_dir):
        from apply_dataform_workflows.apply import compile_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.dry_run = True

        compile_release_configs(mock_client, config, True, github_output)

        mock_client.post.assert_not_called()
        assert github_output.results[0].status == "dry_run"

    def test_compiles_and_patches(self, mock_client, github_output, fixtures_dir):
        from apply_dataform_workflows.apply import compile_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        compile_response = MagicMock()
        compile_response.json.return_value = {
            "name": "projects/p/locations/l/repositories/r/compilationResults/cr-123"
        }
        mock_client.post.return_value = compile_response
        mock_client.patch.return_value = MagicMock(status_code=200)

        compile_release_configs(mock_client, config, True, github_output)

        mock_client.post.assert_called_once_with(
            "/compilationResults",
            body={"releaseConfig": (f"{mock_client.parent}/releaseConfigs/production")},
        )
        mock_client.patch.assert_called_once_with(
            "/releaseConfigs/production",
            body={
                "gitCommitish": "main",
                "releaseCompilationResult": (
                    "projects/p/locations/l/repositories/r/compilationResults/cr-123"
                ),
            },
            params={"updateMask": "gitCommitish,releaseCompilationResult"},
        )
        assert github_output.results[0].status == "success"

    def test_records_compile_failure(self, mock_client, github_output, fixtures_dir):
        from apply_dataform_workflows.apply import compile_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.post.side_effect = ApiError(500, "Compile failed")

        compile_release_configs(mock_client, config, True, github_output)

        assert github_output.results[0].status == "failed"

    def test_records_patch_failure_after_compile(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import compile_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        compile_response = MagicMock()
        compile_response.json.return_value = {
            "name": "projects/p/compilationResults/cr-123"
        }
        mock_client.post.return_value = compile_response
        mock_client.patch.side_effect = ApiError(500, "Patch failed")

        compile_release_configs(mock_client, config, True, github_output)

        assert github_output.results[0].status == "failed"


class TestDeployWorkflowConfigs:
    def test_deploys_with_fqn_resolution(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        # Use an outdated cronSchedule so the update path is exercised.
        mock_client.get.return_value = _json_response(
            {
                "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                "cronSchedule": "0 0 * * *",
                "timeZone": "Asia/Tokyo",
                "invocationConfig": {},
                "disabled": False,
            }
        )

        deploy_workflow_configs(mock_client, config, False, github_output)

        body = mock_client.patch.call_args.args[1]
        assert (
            body["releaseConfig"] == f"{mock_client.parent}/releaseConfigs/production"
        )
        assert body["cronSchedule"] == "0 3 * * *"
        assert body["timeZone"] == "Asia/Tokyo"
        assert body["invocationConfig"] == {}
        assert body["disabled"] is False
        assert "id" not in body
        assert mock_client.patch.call_args.kwargs["params"] == {
            "updateMask": "releaseConfig,cronSchedule,timeZone,disabled"
        }
        assert github_output.results[0].detail == "Updated"

    def test_deploy_merges_targets_and_options_into_invocation_config(
        self, mock_client, github_output, tmp_path
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config_file = tmp_path / "config.json"
        config_file.write_text(
            """
            {
              "repository": "repo",
              "release_configs": [
                {
                  "id": "production",
                  "git_ref": "main"
                }
              ],
              "workflow_configs": [
                {
                  "id": "daily-run",
                  "release_config": "production",
                  "targets": {
                    "actions": ["users"]
                  },
                  "options": {
                    "include_dependencies": true,
                    "full_refresh": true
                  }
                }
              ]
            }
            """
        )
        config = ConfigLoader.load(config_file)
        # invocationConfig matches desired — mutable fields also unchanged → no recreate, no PATCH.
        mock_client.get.return_value = _json_response(
            {
                "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                "invocationConfig": {
                    "includedTargets": [{"name": "users"}],
                    "transitiveDependenciesIncluded": True,
                    "fullyRefreshIncrementalTablesEnabled": True,
                },
                "disabled": False,
            }
        )

        deploy_workflow_configs(mock_client, config, False, github_output)

        mock_client.delete.assert_not_called()
        mock_client.patch.assert_not_called()
        assert github_output.results[0].detail == "No changes"

    def test_deploy_workflow_supports_on_demand_without_schedule(
        self, mock_client, github_output, tmp_path
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config_file = tmp_path / "config.json"
        config_file.write_text(
            """
            {
              "repository": "repo",
              "release_configs": [
                {
                  "id": "production",
                  "git_ref": "main"
                }
              ],
              "workflow_configs": [
                {
                  "id": "daily-run",
                  "release_config": "production",
                  "targets": {
                    "tags": ["daily"]
                  }
                }
              ]
            }
            """
        )
        config = ConfigLoader.load(config_file)
        mock_client.get.return_value = _json_response(
            {
                "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                "invocationConfig": {"includedTags": ["daily"]},
                "disabled": False,
            }
        )

        deploy_workflow_configs(mock_client, config, False, github_output)

        # All mutable fields match existing — no recreate, no PATCH needed.
        mock_client.delete.assert_not_called()
        mock_client.patch.assert_not_called()
        assert github_output.results[0].detail == "No changes"

    def test_dry_run_records_result(self, mock_client, github_output, fixtures_dir):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.dry_run = True
        mock_client.upsert.return_value = UpsertResult.DRY_RUN

        deploy_workflow_configs(mock_client, config, True, github_output)

        assert github_output.results[0].status == "dry_run"

    def test_sets_workflow_config_created_output(
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
        mock_client.get.side_effect = ApiError(404, "Not found")

        deploy_workflow_configs(mock_client, config, False, output)

        content = output_file.read_text()
        assert "workflow_configs_created=daily-run\n" in content
        assert "workflow_configs_updated=\n" in content

    def test_sets_workflow_config_updated_output(
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
        # Use an outdated cronSchedule so the config is detected as changed.
        mock_client.get.return_value = _json_response(
            {
                "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                "cronSchedule": "0 0 * * *",
                "timeZone": "Asia/Tokyo",
                "invocationConfig": {},
                "disabled": False,
            }
        )

        deploy_workflow_configs(mock_client, config, False, output)

        content = output_file.read_text()
        assert "workflow_configs_created=\n" in content
        assert "workflow_configs_updated=daily-run\n" in content

    def test_sync_delete_removes_orphans(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.get.side_effect = [
            _json_response(
                {
                    "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                    "cronSchedule": "0 3 * * *",
                    "timeZone": "Asia/Tokyo",
                    "invocationConfig": {},
                    "disabled": False,
                }
            ),
            _json_response(
                {
                    "workflowConfigs": [
                        {"name": f"{mock_client.parent}/workflowConfigs/daily-run"},
                        {"name": f"{mock_client.parent}/workflowConfigs/old-workflow"},
                    ]
                }
            ),
            _json_response({"releaseConfigs": []}),
        ]

        deploy_workflow_configs(mock_client, config, True, github_output)

        mock_client.delete.assert_called_once_with("/workflowConfigs/old-workflow")
        assert any(result.status == "deleted" for result in github_output.results)

    def test_sync_delete_preserves_listed_configs(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.get.side_effect = [
            _json_response(
                {
                    "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                    "cronSchedule": "0 3 * * *",
                    "timeZone": "Asia/Tokyo",
                    "invocationConfig": {},
                    "disabled": False,
                }
            ),
            _json_response(
                {
                    "workflowConfigs": [
                        {"name": f"{mock_client.parent}/workflowConfigs/daily-run"},
                    ]
                }
            ),
            _json_response({"releaseConfigs": []}),
        ]

        deploy_workflow_configs(mock_client, config, True, github_output)

        mock_client.delete.assert_not_called()

    def test_records_failure_on_upsert_error(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.get.side_effect = ApiError(500, "Server error")

        deploy_workflow_configs(mock_client, config, False, github_output)

        assert github_output.results[0].status == "failed"

    def test_recreates_workflow_config_when_invocation_config_changes(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.get.return_value = _json_response(
            {
                "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                "cronSchedule": "0 3 * * *",
                "timeZone": "Asia/Tokyo",
                "invocationConfig": {"includedTags": ["old-tag"]},
                "disabled": False,
            }
        )

        deploy_workflow_configs(mock_client, config, False, github_output)

        expected_body = {
            "cronSchedule": "0 3 * * *",
            "timeZone": "Asia/Tokyo",
            "invocationConfig": {},
            "disabled": False,
            "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
        }
        assert mock_client.method_calls[:3] == [
            call.get("/workflowConfigs/daily-run"),
            call.delete("/workflowConfigs/daily-run"),
            call.post(
                "/workflowConfigs",
                expected_body,
                params={"workflowConfigId": "daily-run"},
            ),
        ]
        assert github_output.results[0].status == "success"
        assert github_output.results[0].detail == "Recreated"

    def test_does_not_recreate_when_api_injects_bool_defaults_into_invocation_config(
        self, mock_client, github_output, tmp_path
    ):
        """Second run must not recreate when GCP API adds false-default bool fields."""
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config_file = tmp_path / "config.json"
        config_file.write_text(
            """
            {
              "repository": "repo",
              "release_configs": [{"id": "production", "git_ref": "main"}],
              "workflow_configs": [
                {
                  "id": "daily",
                  "release_config": "production",
                  "targets": {"tags": ["daily"]}
                }
              ]
            }
            """
        )
        config = ConfigLoader.load(config_file)
        mock_client.get.return_value = _json_response(
            {
                "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                "invocationConfig": {
                    "includedTags": ["daily"],
                    "transitiveDependenciesIncluded": False,
                    "transitiveDependentsIncluded": False,
                    "fullyRefreshIncrementalTablesEnabled": False,
                },
                "disabled": False,
            }
        )

        deploy_workflow_configs(mock_client, config, False, github_output)

        mock_client.delete.assert_not_called()
        assert github_output.results[0].detail == "No changes"

    def test_does_not_recreate_when_api_injects_query_priority_into_invocation_config(
        self, mock_client, github_output, tmp_path
    ):
        """GCP injects queryPriority: QUERY_PRIORITY_UNSPECIFIED; must not cause recreate."""
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config_file = tmp_path / "config.json"
        config_file.write_text(
            """
            {
              "repository": "repo",
              "release_configs": [{"id": "production", "git_ref": "main"}],
              "workflow_configs": [
                {
                  "id": "daily",
                  "release_config": "production",
                  "targets": {"tags": ["daily"]}
                }
              ]
            }
            """
        )
        config = ConfigLoader.load(config_file)
        mock_client.get.return_value = _json_response(
            {
                "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                "invocationConfig": {
                    "includedTags": ["daily"],
                    "queryPriority": "QUERY_PRIORITY_UNSPECIFIED",
                },
                "disabled": False,
            }
        )

        deploy_workflow_configs(mock_client, config, False, github_output)

        mock_client.delete.assert_not_called()
        assert github_output.results[0].detail == "No changes"

    def test_does_not_recreate_when_api_injects_all_defaults_into_empty_invocation_config(
        self, mock_client, github_output, tmp_path
    ):
        """is_all target produces empty invocationConfig; API defaults must not cause recreate."""
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config_file = tmp_path / "config.json"
        config_file.write_text(
            """
            {
              "repository": "repo",
              "release_configs": [{"id": "production", "git_ref": "main"}],
              "workflow_configs": [
                {
                  "id": "all",
                  "release_config": "production",
                  "targets": {"is_all": true}
                }
              ]
            }
            """
        )
        config = ConfigLoader.load(config_file)
        mock_client.get.return_value = _json_response(
            {
                "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                "invocationConfig": {
                    "queryPriority": "QUERY_PRIORITY_UNSPECIFIED",
                },
                "disabled": False,
            }
        )

        deploy_workflow_configs(mock_client, config, False, github_output)

        mock_client.delete.assert_not_called()
        assert github_output.results[0].detail == "No changes"

    def test_recreates_when_invocation_config_bool_field_explicitly_set_to_true(
        self, mock_client, github_output, tmp_path
    ):
        """If desired sets a bool field to true, changing it to false must trigger recreate."""
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config_file = tmp_path / "config.json"
        config_file.write_text(
            """
            {
              "repository": "repo",
              "release_configs": [{"id": "production", "git_ref": "main"}],
              "workflow_configs": [
                {
                  "id": "daily",
                  "release_config": "production",
                  "targets": {"tags": ["daily"]},
                  "options": {"include_dependencies": true}
                }
              ]
            }
            """
        )
        config = ConfigLoader.load(config_file)
        mock_client.get.return_value = _json_response(
            {
                "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                "invocationConfig": {
                    "includedTags": ["daily"],
                    "transitiveDependenciesIncluded": False,
                },
                "disabled": False,
            }
        )

        deploy_workflow_configs(mock_client, config, False, github_output)

        mock_client.delete.assert_called_once()
        assert github_output.results[0].detail == "Recreated"

    def test_records_failure_when_workflow_recreate_fails(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.get.return_value = _json_response(
            {
                "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                "cronSchedule": "0 3 * * *",
                "timeZone": "Asia/Tokyo",
                "invocationConfig": {"includedTags": ["old-tag"]},
                "disabled": False,
            }
        )
        mock_client.post.side_effect = ApiError(500, "Recreate failed")

        deploy_workflow_configs(mock_client, config, False, github_output)

        expected_body = {
            "cronSchedule": "0 3 * * *",
            "timeZone": "Asia/Tokyo",
            "invocationConfig": {},
            "disabled": False,
            "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
        }
        assert mock_client.method_calls[:3] == [
            call.get("/workflowConfigs/daily-run"),
            call.delete("/workflowConfigs/daily-run"),
            call.post(
                "/workflowConfigs",
                expected_body,
                params={"workflowConfigId": "daily-run"},
            ),
        ]
        assert github_output.results[0].status == "failed"
        assert github_output.results[0].detail == "Failed"

    def test_sync_delete_warns_on_list_failure(
        self, mock_client, github_output, fixtures_dir, capsys
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.get.side_effect = [
            _json_response(
                {
                    "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                    "cronSchedule": "0 3 * * *",
                    "timeZone": "Asia/Tokyo",
                    "invocationConfig": {},
                    "disabled": False,
                }
            ),
            ApiError(500, "List failed"),
            _json_response({"releaseConfigs": []}),
        ]

        deploy_workflow_configs(mock_client, config, True, github_output)

        captured = capsys.readouterr()
        assert "::warning::" in captured.out
        assert "List failed" in captured.out
        mock_client.delete.assert_not_called()

    def test_sync_delete_removes_orphaned_release_configs(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.get.side_effect = [
            _json_response(
                {
                    "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                    "cronSchedule": "0 3 * * *",
                    "timeZone": "Asia/Tokyo",
                    "invocationConfig": {},
                    "disabled": False,
                }
            ),
            _json_response(
                {
                    "workflowConfigs": [
                        {"name": f"{mock_client.parent}/workflowConfigs/daily-run"},
                    ]
                }
            ),
            _json_response(
                {
                    "releaseConfigs": [
                        {"name": f"{mock_client.parent}/releaseConfigs/production"},
                        {"name": f"{mock_client.parent}/releaseConfigs/old-release"},
                    ]
                }
            ),
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
            _json_response(
                {
                    "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                    "cronSchedule": "0 3 * * *",
                    "timeZone": "Asia/Tokyo",
                    "invocationConfig": {},
                    "disabled": False,
                }
            ),
            _json_response(
                {
                    "workflowConfigs": [
                        {"name": f"{mock_client.parent}/workflowConfigs/daily-run"},
                        {"name": f"{mock_client.parent}/workflowConfigs/old-workflow"},
                    ]
                }
            ),
            _json_response(
                {
                    "releaseConfigs": [
                        {"name": f"{mock_client.parent}/releaseConfigs/production"},
                        {"name": f"{mock_client.parent}/releaseConfigs/old-release"},
                    ]
                }
            ),
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
            _json_response(
                {
                    "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                    "cronSchedule": "0 3 * * *",
                    "timeZone": "Asia/Tokyo",
                    "invocationConfig": {},
                    "disabled": False,
                }
            ),
            _json_response(
                {
                    "workflowConfigs": [
                        {"name": f"{mock_client.parent}/workflowConfigs/daily-run"},
                    ]
                }
            ),
            _json_response(
                {
                    "releaseConfigs": [
                        {"name": f"{mock_client.parent}/releaseConfigs/production"},
                        {"name": f"{mock_client.parent}/releaseConfigs/old-release"},
                    ]
                }
            ),
        ]

        deploy_workflow_configs(mock_client, config, True, output)

        content = output_file.read_text()
        assert "release_configs_deleted=old-release\n" in content
        assert "release_configs_delete_failed=\n" in content

    def test_workflow_config_disabled_true_sets_disabled_and_update_mask(
        self, mock_client, github_output, tmp_path
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config_file = tmp_path / "config.json"
        config_file.write_text(
            """
            {
              "repository": "repo",
              "release_configs": [
                {
                  "id": "production",
                  "git_ref": "main"
                }
              ],
              "workflow_configs": [
                {
                  "id": "daily-run",
                  "release_config": "production",
                  "disabled": true,
                  "targets": {
                    "tags": ["daily"]
                  }
                }
              ]
            }
            """
        )
        config = ConfigLoader.load(config_file)
        # existing has disabled=False so the change to True triggers a PATCH.
        mock_client.get.return_value = _json_response(
            {
                "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                "invocationConfig": {"includedTags": ["daily"]},
                "disabled": False,
            }
        )

        deploy_workflow_configs(mock_client, config, False, github_output)

        assert mock_client.patch.call_args.args[1] == {
            "invocationConfig": {"includedTags": ["daily"]},
            "disabled": True,
            "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
        }
        assert mock_client.patch.call_args.kwargs["params"] == {
            "updateMask": "releaseConfig,disabled"
        }

    def test_workflow_config_without_disabled_defaults_disabled_and_update_mask(
        self, mock_client, github_output, tmp_path
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config_file = tmp_path / "config.json"
        config_file.write_text(
            """
            {
              "repository": "repo",
              "release_configs": [
                {
                  "id": "production",
                  "git_ref": "main"
                }
              ],
              "workflow_configs": [
                {
                  "id": "daily-run",
                  "release_config": "production",
                  "targets": {
                    "tags": ["daily"]
                  }
                }
              ]
            }
            """
        )
        config = ConfigLoader.load(config_file)
        # existing has disabled=True so the change to False (default) triggers a PATCH.
        mock_client.get.return_value = _json_response(
            {
                "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
                "invocationConfig": {"includedTags": ["daily"]},
                "disabled": True,
            }
        )

        deploy_workflow_configs(mock_client, config, False, github_output)

        assert mock_client.patch.call_args.args[1] == {
            "invocationConfig": {"includedTags": ["daily"]},
            "disabled": False,
            "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
        }
        assert mock_client.patch.call_args.kwargs["params"] == {
            "updateMask": "releaseConfig,disabled"
        }


class TestMain:
    def _env(self, overrides=None):
        base = {
            "CONFIG_FILE": "tests/fixtures/config_simple.json",
            "WORKFLOW_SETTINGS": "tests/fixtures/workflow_settings.yaml",
            "PROJECT_ID": "test-project",
            "LOCATION": "asia-northeast1",
            "DO_COMPILE": "false",
            "SYNC_DELETE": "true",
            "DRY_RUN": "true",
            "GITHUB_OUTPUT": "/dev/null",
            "GITHUB_STEP_SUMMARY": "/dev/null",
        }
        if overrides:
            base.update(overrides)
        return base

    def test_missing_config_file_exits(self, monkeypatch):
        from apply_dataform_workflows.apply import main

        env = self._env({"CONFIG_FILE": "nonexistent.json"})
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        with pytest.raises(SystemExit):
            main()

    @mock_patch("apply_dataform_workflows.apply.DataformApiClient")
    def test_uses_repository_from_config(self, mock_client_cls, monkeypatch):
        from apply_dataform_workflows.apply import main

        env = self._env()
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        mock_client = MagicMock()
        mock_client.parent = (
            "projects/test-project/locations/asia-northeast1/repositories/"
            "my-dataform-repo"
        )
        mock_client.dry_run = True
        mock_client.upsert.return_value = UpsertResult.DRY_RUN
        mock_client_cls.return_value = mock_client

        main()

        mock_client_cls.assert_called_once_with(
            project_id="test-project",
            location="asia-northeast1",
            repository="my-dataform-repo",
            api_version="v1",
            dry_run=True,
        )

    @mock_patch("apply_dataform_workflows.apply.DataformApiClient")
    def test_dry_run_completes_successfully(self, mock_client_cls, monkeypatch):
        from apply_dataform_workflows.apply import main

        env = self._env()
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        mock_client = MagicMock()
        mock_client.parent = (
            "projects/test-project/locations/asia-northeast1/repositories/test-repo"
        )
        mock_client.dry_run = True
        mock_client.upsert.return_value = UpsertResult.DRY_RUN
        mock_client_cls.return_value = mock_client

        main()

    @mock_patch("apply_dataform_workflows.apply.DataformApiClient")
    def test_non_dry_run_completes_successfully(self, mock_client_cls, monkeypatch):
        from apply_dataform_workflows.apply import main

        env = self._env({"DRY_RUN": "false"})
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        mock_client = MagicMock()
        mock_client.parent = (
            "projects/test-project/locations/asia-northeast1/repositories/test-repo"
        )
        mock_client.dry_run = False
        mock_client.upsert.return_value = UpsertResult.UPDATED
        mock_client.get.return_value = MagicMock(
            json=MagicMock(return_value={"releaseConfigs": [], "workflowConfigs": []})
        )
        mock_client_cls.return_value = mock_client

        main()

    @mock_patch("apply_dataform_workflows.apply.DataformApiClient")
    def test_normalizes_location_env_us_and_emits_warning(
        self, mock_client_cls, monkeypatch, capsys
    ):
        from apply_dataform_workflows.apply import main

        env = self._env({"LOCATION": "US"})
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        mock_client = MagicMock()
        mock_client.parent = (
            "projects/test-project/locations/us-central1/repositories/test-repo"
        )
        mock_client.dry_run = True
        mock_client.upsert.return_value = UpsertResult.DRY_RUN
        mock_client_cls.return_value = mock_client

        main()

        mock_client_cls.assert_called_once_with(
            project_id="test-project",
            location="us-central1",
            repository="my-dataform-repo",
            api_version="v1",
            dry_run=True,
        )
        captured = capsys.readouterr()
        assert (
            "::warning::Multi-region location 'US' is not supported by Dataform. "
            "Automatically converted to 'us-central1'."
        ) in captured.out

    @mock_patch("apply_dataform_workflows.apply.DataformApiClient")
    def test_normalizes_yaml_default_location_eu_and_emits_warning(
        self, mock_client_cls, monkeypatch, tmp_path, capsys
    ):
        from apply_dataform_workflows.apply import main

        workflow_settings = tmp_path / "workflow_settings.yaml"
        workflow_settings.write_text(
            "defaultProject: test-project\ndefaultLocation: EU\n"
        )
        env = self._env(
            {
                "WORKFLOW_SETTINGS": str(workflow_settings),
                "PROJECT_ID": "",
                "LOCATION": "",
            }
        )
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        mock_client = MagicMock()
        mock_client.parent = (
            "projects/test-project/locations/europe-west1/repositories/test-repo"
        )
        mock_client.dry_run = True
        mock_client.upsert.return_value = UpsertResult.DRY_RUN
        mock_client_cls.return_value = mock_client

        main()

        mock_client_cls.assert_called_once_with(
            project_id="test-project",
            location="europe-west1",
            repository="my-dataform-repo",
            api_version="v1",
            dry_run=True,
        )
        captured = capsys.readouterr()
        assert (
            "::warning::Multi-region location 'EU' is not supported by Dataform. "
            "Automatically converted to 'europe-west1'."
        ) in captured.out

    @mock_patch("apply_dataform_workflows.apply.DataformApiClient")
    def test_preserves_single_region_location_without_warning(
        self, mock_client_cls, monkeypatch, capsys
    ):
        from apply_dataform_workflows.apply import main

        env = self._env({"LOCATION": "us-east4"})
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        mock_client = MagicMock()
        mock_client.parent = (
            "projects/test-project/locations/us-east4/repositories/test-repo"
        )
        mock_client.dry_run = True
        mock_client.upsert.return_value = UpsertResult.DRY_RUN
        mock_client_cls.return_value = mock_client

        main()

        mock_client_cls.assert_called_once_with(
            project_id="test-project",
            location="us-east4",
            repository="my-dataform-repo",
            api_version="v1",
            dry_run=True,
        )
        captured = capsys.readouterr()
        assert "Multi-region location" not in captured.out

    def test_empty_release_configs_without_flag_prints_error(
        self, monkeypatch, tmp_path, capsys
    ):
        from apply_dataform_workflows.apply import main

        config_file = tmp_path / "config.json"
        config_file.write_text('{"repository": "repo", "release_configs": []}')
        env = self._env(
            {
                "CONFIG_FILE": str(config_file),
                "ALLOW_EMPTY_CONFIG": "false",
            }
        )
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
        env = self._env(
            {
                "CONFIG_FILE": str(config_file),
                "ALLOW_EMPTY_CONFIG": "true",
                "DRY_RUN": "false",
                "SYNC_DELETE": "false",
            }
        )
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        mock_client = MagicMock()
        mock_client.parent = (
            "projects/test-project/locations/asia-northeast1/repositories/repo"
        )
        mock_client.dry_run = False
        mock_client_cls.return_value = mock_client

        main()  # must not raise

    def test_empty_release_configs_with_nonempty_workflow_configs_exits(
        self, monkeypatch, tmp_path, capsys
    ):
        from apply_dataform_workflows.apply import main

        config_file = tmp_path / "config.json"
        config_file.write_text(
            '{"repository": "repo", "release_configs": [], "workflow_configs": ['
            '{"id": "daily", "release_config": "prod", "targets": {"is_all": true}}'
            "]}"
        )
        env = self._env(
            {
                "CONFIG_FILE": str(config_file),
                "ALLOW_EMPTY_CONFIG": "true",
            }
        )
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        with pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert "workflow_configs" in captured.out
