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


class TestDeployReleaseConfigs:
    def test_deploys_single_release_config(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.upsert.return_value = UpsertResult.UPDATED

        deploy_release_configs(mock_client, config, False, github_output)

        mock_client.upsert.assert_called_once_with(
            "releaseConfig",
            "production",
            "/releaseConfigs",
            "releaseConfigId",
            {
                "gitCommitish": "main",
                "cronSchedule": "0 0 * * *",
                "timeZone": "Asia/Tokyo",
                "disabled": False,
            },
            update_mask="gitCommitish,cronSchedule,timeZone,disabled",
        )
        assert any(result.status == "success" for result in github_output.results)
        assert github_output.results[0].detail == "Updated"

    def test_deploys_multiple_release_configs(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_advanced.json")
        mock_client.upsert.return_value = UpsertResult.CREATED

        deploy_release_configs(mock_client, config, False, github_output)

        assert mock_client.upsert.call_count == 2
        first_body = mock_client.upsert.call_args_list[0].args[4]
        second_body = mock_client.upsert.call_args_list[1].args[4]
        assert first_body == {
            "gitCommitish": "main",
            "cronSchedule": "0 0 * * *",
            "timeZone": "Asia/Tokyo",
            "disabled": False,
        }
        assert (
            mock_client.upsert.call_args_list[0].kwargs["update_mask"]
            == "gitCommitish,cronSchedule,timeZone,disabled"
        )
        assert second_body == {
            "gitCommitish": "develop",
            "codeCompilationConfig": {
                "defaultDatabase": "my-project-dev",
                "schemaSuffix": "_dev",
                "vars": {"env": "development"},
            },
            "disabled": False,
        }
        assert (
            mock_client.upsert.call_args_list[1].kwargs["update_mask"]
            == "gitCommitish,codeCompilationConfig,disabled"
        )
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
        mock_client.upsert.return_value = UpsertResult.CREATED

        deploy_release_configs(mock_client, config, False, output)

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
        mock_client.upsert.return_value = UpsertResult.UPDATED

        deploy_release_configs(mock_client, config, False, output)

        content = output_file.read_text()
        assert "release_configs_created=\n" in content
        assert "release_configs_updated=production\n" in content

    def test_records_dry_run(self, mock_client, github_output, fixtures_dir):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.upsert.return_value = UpsertResult.DRY_RUN

        deploy_release_configs(mock_client, config, False, github_output)

        assert github_output.results[0].status == "dry_run"

    def test_records_failure_on_api_error(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.upsert.side_effect = ApiError(500, "Server error")

        deploy_release_configs(mock_client, config, False, github_output)

        assert github_output.results[0].status == "failed"

    def test_strips_id_from_body(self, mock_client, github_output, fixtures_dir):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.upsert.return_value = UpsertResult.UPDATED

        deploy_release_configs(mock_client, config, False, github_output)

        body = mock_client.upsert.call_args.args[4]
        assert "id" not in body

    def test_sync_delete_removes_orphans(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.upsert.return_value = UpsertResult.UPDATED
        list_response = MagicMock()
        list_response.json.return_value = {
            "releaseConfigs": [
                {"name": f"{mock_client.parent}/releaseConfigs/production"},
                {"name": f"{mock_client.parent}/releaseConfigs/old-release"},
            ]
        }
        mock_client.get.return_value = list_response

        deploy_release_configs(mock_client, config, True, github_output)

        mock_client.delete.assert_called_once_with("/releaseConfigs/old-release")
        assert any(result.status == "deleted" for result in github_output.results)

    def test_sync_delete_preserves_listed_configs(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.upsert.return_value = UpsertResult.UPDATED
        list_response = MagicMock()
        list_response.json.return_value = {
            "releaseConfigs": [
                {"name": f"{mock_client.parent}/releaseConfigs/production"},
            ]
        }
        mock_client.get.return_value = list_response

        deploy_release_configs(mock_client, config, True, github_output)

        mock_client.delete.assert_not_called()

    def test_sync_delete_warns_on_list_failure(
        self, mock_client, github_output, fixtures_dir, capsys
    ):
        from apply_dataform_workflows.apply import deploy_release_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.upsert.return_value = UpsertResult.UPDATED
        mock_client.get.side_effect = ApiError(500, "List failed")

        deploy_release_configs(mock_client, config, True, github_output)

        captured = capsys.readouterr()
        assert "::warning::" in captured.out
        assert "List failed" in captured.out
        mock_client.delete.assert_not_called()

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
        mock_client.upsert.return_value = UpsertResult.UPDATED

        deploy_release_configs(mock_client, config, False, github_output)

        assert mock_client.upsert.call_args.args[4] == {
            "gitCommitish": "main",
            "disabled": True,
        }
        assert (
            mock_client.upsert.call_args.kwargs["update_mask"]
            == "gitCommitish,disabled"
        )

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
        mock_client.upsert.return_value = UpsertResult.UPDATED

        deploy_release_configs(mock_client, config, False, github_output)

        assert mock_client.upsert.call_args.args[4] == {
            "gitCommitish": "main",
            "disabled": False,
        }
        assert (
            mock_client.upsert.call_args.kwargs["update_mask"]
            == "gitCommitish,disabled"
        )


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
        mock_client.upsert.return_value = UpsertResult.UPDATED

        deploy_workflow_configs(mock_client, config, False, github_output)

        body = mock_client.upsert.call_args.args[4]
        assert (
            body["releaseConfig"] == f"{mock_client.parent}/releaseConfigs/production"
        )
        assert body["cronSchedule"] == "0 3 * * *"
        assert body["timeZone"] == "Asia/Tokyo"
        assert body["invocationConfig"] == {}
        assert body["disabled"] is False
        assert "id" not in body
        assert (
            mock_client.upsert.call_args.kwargs["update_mask"]
            == "releaseConfig,cronSchedule,timeZone,invocationConfig,disabled"
        )
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
        mock_client.upsert.return_value = UpsertResult.UPDATED

        deploy_workflow_configs(mock_client, config, False, github_output)

        body = mock_client.upsert.call_args.args[4]
        assert body["invocationConfig"] == {
            "includedTargets": [{"name": "users"}],
            "transitiveDependenciesIncluded": True,
            "fullyRefreshIncrementalTablesEnabled": True,
        }
        assert body["disabled"] is False
        assert body["invocationConfig"]["includedTargets"][0]["name"] == "users"
        assert (
            mock_client.upsert.call_args.kwargs["update_mask"]
            == "releaseConfig,invocationConfig,disabled"
        )

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
        mock_client.upsert.return_value = UpsertResult.UPDATED

        deploy_workflow_configs(mock_client, config, False, github_output)

        body = mock_client.upsert.call_args.args[4]
        assert "cronSchedule" not in body
        assert "timeZone" not in body
        assert body["invocationConfig"] == {"includedTags": ["daily"]}
        assert body["disabled"] is False
        assert (
            mock_client.upsert.call_args.kwargs["update_mask"]
            == "releaseConfig,invocationConfig,disabled"
        )

    def test_dry_run_records_result(self, mock_client, github_output, fixtures_dir):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
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
        mock_client.upsert.return_value = UpsertResult.CREATED

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
        mock_client.upsert.return_value = UpsertResult.UPDATED

        deploy_workflow_configs(mock_client, config, False, output)

        content = output_file.read_text()
        assert "workflow_configs_created=\n" in content
        assert "workflow_configs_updated=daily-run\n" in content

    def test_sync_delete_removes_orphans(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.upsert.return_value = UpsertResult.UPDATED
        list_response = MagicMock()
        list_response.json.return_value = {
            "workflowConfigs": [
                {"name": f"{mock_client.parent}/workflowConfigs/daily-run"},
                {"name": f"{mock_client.parent}/workflowConfigs/old-workflow"},
            ]
        }
        mock_client.get.return_value = list_response

        deploy_workflow_configs(mock_client, config, True, github_output)

        mock_client.delete.assert_called_once_with("/workflowConfigs/old-workflow")
        assert any(result.status == "deleted" for result in github_output.results)

    def test_sync_delete_preserves_listed_configs(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.upsert.return_value = UpsertResult.UPDATED
        list_response = MagicMock()
        list_response.json.return_value = {
            "workflowConfigs": [
                {"name": f"{mock_client.parent}/workflowConfigs/daily-run"},
            ]
        }
        mock_client.get.return_value = list_response

        deploy_workflow_configs(mock_client, config, True, github_output)

        mock_client.delete.assert_not_called()

    def test_records_failure_on_upsert_error(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.upsert.side_effect = ApiError(500, "Server error")

        deploy_workflow_configs(mock_client, config, False, github_output)

        assert github_output.results[0].status == "failed"

    def test_recreates_workflow_config_on_immutable_field_error(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.upsert.side_effect = ApiError(
            400, "Request contains immutable fields"
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
            call.upsert(
                "workflowConfig",
                "daily-run",
                "/workflowConfigs",
                "workflowConfigId",
                expected_body,
                update_mask=(
                    "releaseConfig,cronSchedule,timeZone,invocationConfig,disabled"
                ),
            ),
            call.delete("/workflowConfigs/daily-run"),
            call.post(
                "/workflowConfigs",
                expected_body,
                params={"workflowConfigId": "daily-run"},
            ),
        ]
        assert github_output.results[0].status == "success"
        assert github_output.results[0].detail == "Recreated"

    def test_records_failure_when_recreate_after_immutable_field_error_fails(
        self, mock_client, github_output, fixtures_dir
    ):
        from apply_dataform_workflows.apply import deploy_workflow_configs

        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        mock_client.upsert.side_effect = ApiError(
            400, "Request contains immutable fields"
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
            call.upsert(
                "workflowConfig",
                "daily-run",
                "/workflowConfigs",
                "workflowConfigId",
                expected_body,
                update_mask=(
                    "releaseConfig,cronSchedule,timeZone,invocationConfig,disabled"
                ),
            ),
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
        mock_client.upsert.return_value = UpsertResult.UPDATED
        mock_client.get.side_effect = ApiError(500, "List failed")

        deploy_workflow_configs(mock_client, config, True, github_output)

        captured = capsys.readouterr()
        assert "::warning::" in captured.out
        assert "List failed" in captured.out
        mock_client.delete.assert_not_called()

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
        mock_client.upsert.return_value = UpsertResult.UPDATED

        deploy_workflow_configs(mock_client, config, False, github_output)

        assert mock_client.upsert.call_args.args[4] == {
            "invocationConfig": {"includedTags": ["daily"]},
            "disabled": True,
            "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
        }
        assert (
            mock_client.upsert.call_args.kwargs["update_mask"]
            == "releaseConfig,invocationConfig,disabled"
        )

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
        mock_client.upsert.return_value = UpsertResult.UPDATED

        deploy_workflow_configs(mock_client, config, False, github_output)

        assert mock_client.upsert.call_args.args[4] == {
            "invocationConfig": {"includedTags": ["daily"]},
            "disabled": False,
            "releaseConfig": f"{mock_client.parent}/releaseConfigs/production",
        }
        assert (
            mock_client.upsert.call_args.kwargs["update_mask"]
            == "releaseConfig,invocationConfig,disabled"
        )


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
