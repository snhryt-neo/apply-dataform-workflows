from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

from apply_dataform_workflows.client import ApiError, DataformApiClient
from apply_dataform_workflows.config import (
    ConfigLoader,
    DeployConfig,
    normalize_location,
)


@dataclass
class StepResult:
    step: str
    resource: str
    status: str
    detail: str


class GitHubOutput:
    def __init__(self, output_path: str = "/dev/null", summary_path: str = "/dev/null"):
        self._output_path = output_path
        self._summary_path = summary_path
        self.results: list[StepResult] = []

    @classmethod
    def from_env(cls) -> GitHubOutput:
        return cls(
            output_path=os.environ.get("GITHUB_OUTPUT", "/dev/null"),
            summary_path=os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"),
        )

    def set_output(self, key: str, value: str) -> None:
        if "\n" in key or "=" in key:
            raise ValueError(
                f"GITHUB_OUTPUT key must not contain '\\n' or '=': {key!r}"
            )
        if "\n" in value:
            raise ValueError(
                f"GITHUB_OUTPUT value must not contain newlines: {value!r}"
            )
        with open(self._output_path, "a") as f:
            f.write(f"{key}={value}\n")

    def add_result(self, result: StepResult) -> None:
        self.results.append(result)

    @property
    def has_failure(self) -> bool:
        return any(r.status == "failed" for r in self.results)

    @property
    def has_success(self) -> bool:
        return any(r.status == "success" for r in self.results)

    @property
    def deployment_status(self) -> str:
        if self.has_failure and self.has_success:
            return "partial_success"
        elif self.has_failure:
            return "failure"
        return "success"

    def write_summary(self) -> None:
        lines = [
            "## Apply Dataform Release / Workflow Configurations",
            "",
            "| Step | Resource | Status |",
            "|------|----------|--------|",
        ]
        status_icons = {
            "success": "✅",
            "failed": "❌",
            "skipped": "—",
            "dry_run": "🔵",
            "deleted": "🗑️",
            "no_changes": "✓",
        }
        for r in self.results:
            icon = status_icons.get(r.status, "")
            lines.append(f"| {r.step} | {r.resource} | {icon} {r.detail} |")

        with open(self._summary_path, "a") as f:
            f.write("\n".join(lines) + "\n")


def _build_update_mask(body: dict, allowed_fields: tuple[str, ...]) -> str:
    return ",".join(field for field in allowed_fields if field in body)


def _filter_body_fields(body: dict, allowed_fields: tuple[str, ...]) -> dict:
    return {field: body[field] for field in allowed_fields if field in body}


def _field_unchanged(existing: dict, desired: dict, key: str) -> bool:
    e, d = existing.get(key), desired.get(key)
    # GCP omits boolean fields from GET responses when they equal their default (False).
    if e is None and isinstance(d, bool):
        e = False
    return e == d


def _get_existing_resource(
    client: DataformApiClient, resource_path: str
) -> dict | None:
    try:
        return client.get(resource_path).json()
    except ApiError as error:
        if error.status_code == 404:
            return None
        raise


def _release_config_requires_recreate(existing: dict, desired: dict) -> bool:
    # The Dataform API exposes PATCH for release configs, but updates to
    # gitCommitish and codeCompilationConfig are safer to handle via explicit
    # delete + recreate than by relying on a later immutable-field error.
    return existing.get("gitCommitish") != desired.get("gitCommitish") or existing.get(
        "codeCompilationConfig"
    ) != desired.get("codeCompilationConfig")


_INVOCATION_CONFIG_API_DEFAULTS: dict[str, object] = {
    "transitiveDependenciesIncluded": False,
    "transitiveDependentsIncluded": False,
    "fullyRefreshIncrementalTablesEnabled": False,
    "queryPriority": "QUERY_PRIORITY_UNSPECIFIED",
}


def _normalize_invocation_config(invoc: dict, reference: dict) -> dict:
    # The GCP API injects extra fields (e.g. queryPriority, bool flags) into
    # invocationConfig on GET. Strip them from `invoc` when they are absent
    # from `reference` and carry the API default, so the comparison does not
    # trigger a false recreate on unchanged configs.
    return {
        k: v
        for k, v in invoc.items()
        if k in reference or v != _INVOCATION_CONFIG_API_DEFAULTS.get(k)
    }


def _workflow_config_requires_recreate(existing: dict, desired: dict) -> bool:
    # invocationConfig follows the same pattern: detect the change before PATCH
    # so the recreate path is deterministic and visible in the apply flow.
    desired_invoc = desired.get("invocationConfig") or {}
    existing_invoc = existing.get("invocationConfig") or {}
    normalized = _normalize_invocation_config(existing_invoc, desired_invoc)
    return normalized != desired_invoc


def deploy_release_configs(
    client: DataformApiClient,
    config: DeployConfig,
    output: GitHubOutput,
) -> None:
    print("")
    print("::group::Step 1/3 — Release configurations")

    created = []
    updated = []
    failed = []

    for rc in config.release_configs:
        resource_path = f"/releaseConfigs/{rc.id}"
        try:
            if client.dry_run:
                update_mask = _build_update_mask(
                    rc.body,
                    (
                        "gitCommitish",
                        "cronSchedule",
                        "timeZone",
                        "codeCompilationConfig",
                        "disabled",
                    ),
                )
                client.upsert(
                    "releaseConfig",
                    rc.id,
                    "/releaseConfigs",
                    "releaseConfigId",
                    rc.body,
                    update_mask=update_mask,
                )
                output.add_result(
                    StepResult("1/3", f"releaseConfig: {rc.id}", "dry_run", "Dry run")
                )
                continue

            existing = _get_existing_resource(client, resource_path)
            status = "success"
            if existing is None:
                print(f"  Creating releaseConfig: {rc.id}")
                client.post(
                    "/releaseConfigs", rc.body, params={"releaseConfigId": rc.id}
                )
                print(f"  Created releaseConfig: {rc.id}")
                created.append(rc.id)
                detail = "Created"
            elif _release_config_requires_recreate(existing, rc.body):
                print(
                    "  releaseConfig"
                    f" '{rc.id}' changes immutable-update fields"
                    " (gitCommitish/codeCompilationConfig)."
                    " Deleting and recreating..."
                )
                client.delete(resource_path)
                print(f"  Deleted releaseConfig: {rc.id}")
                client.post(
                    "/releaseConfigs", rc.body, params={"releaseConfigId": rc.id}
                )
                print(f"  Recreated releaseConfig: {rc.id}")
                created.append(rc.id)
                detail = "Recreated"
            else:
                patch_body = _filter_body_fields(
                    rc.body,
                    (
                        "gitCommitish",
                        "cronSchedule",
                        "timeZone",
                        "disabled",
                    ),
                )
                patch_fields = ("cronSchedule", "timeZone", "disabled")
                if all(_field_unchanged(existing, patch_body, f) for f in patch_fields):
                    print(f"  releaseConfig '{rc.id}' is up to date")
                    status = "no_changes"
                    detail = "No changes"
                else:
                    print(f"  Updating releaseConfig: {rc.id}")
                    update_mask = _build_update_mask(patch_body, patch_fields)
                    client.patch(
                        resource_path, patch_body, params={"updateMask": update_mask}
                    )
                    print(f"  Updated releaseConfig: {rc.id}")
                    updated.append(rc.id)
                    detail = "Updated"

            output.add_result(
                StepResult("1/3", f"releaseConfig: {rc.id}", status, detail)
            )
        except ApiError as e:
            print(f"::error::Failed to deploy releaseConfig '{rc.id}': {e.message}")
            failed.append(rc.id)
            output.add_result(
                StepResult("1/3", f"releaseConfig: {rc.id}", "failed", "Failed")
            )

    output.set_output("release_configs_created", ",".join(created))
    output.set_output("release_configs_updated", ",".join(updated))
    output.set_output("release_configs_failed", ",".join(failed))
    print("::endgroup::")


def compile_release_configs(
    client: DataformApiClient,
    config: DeployConfig,
    do_compile: bool,
    output: GitHubOutput,
) -> None:
    print("")
    print("::group::Step 2/3 — Compile release configurations")

    if not do_compile:
        print("  (skipped — compile=false)")
        output.add_result(StepResult("2/3", "Compile", "skipped", "Skipped"))
        print("::endgroup::")
        return

    for rc in config.release_configs:
        rc_name = f"{client.parent}/releaseConfigs/{rc.id}"

        if client.dry_run:
            print(f"  [dry-run] Would compile & update: {rc.id}")
            output.add_result(
                StepResult("2/3", f"compile: {rc.id}", "dry_run", "Dry run")
            )
            continue

        try:
            print(f"  Compiling: {rc.id}")
            compile_response = client.post(
                "/compilationResults", body={"releaseConfig": rc_name}
            )
            compilation_name = compile_response.json()["name"]
            print(f"  Compiled: {compilation_name}")

            git_commitish = rc.body.get("gitCommitish", "")
            client.patch(
                f"/releaseConfigs/{rc.id}",
                body={
                    "gitCommitish": git_commitish,
                    "releaseCompilationResult": compilation_name,
                },
                params={"updateMask": "gitCommitish,releaseCompilationResult"},
            )
            print(f"  Release config updated with latest compilation: {rc.id}")
            output.add_result(
                StepResult("2/3", f"compile: {rc.id}", "success", "Compiled")
            )
        except ApiError as e:
            print(f"::error::Failed to compile '{rc.id}': {e.message}")
            output.add_result(StepResult("2/3", f"compile: {rc.id}", "failed", str(e)))

    print("::endgroup::")


def deploy_workflow_configs(
    client: DataformApiClient,
    config: DeployConfig,
    sync_delete: bool,
    output: GitHubOutput,
) -> None:
    print("")
    print("::group::Step 3/3 — Workflow configurations")

    if not config.workflow_configs and not sync_delete:
        print("  (skipped — no workflow_configs in config)")
        output.set_output("workflow_configs_created", "")
        output.set_output("workflow_configs_updated", "")
        output.set_output("workflow_configs_failed", "")
        output.set_output("workflow_configs_deleted", "")
        output.set_output("workflow_configs_delete_failed", "")
        output.set_output("release_configs_deleted", "")
        output.set_output("release_configs_delete_failed", "")
        output.add_result(
            StepResult("3/3", "Workflow configurations", "skipped", "Skipped")
        )
        print("::endgroup::")
        return

    if not config.workflow_configs and sync_delete:
        print("  (no workflow_configs in config — checking for orphans to delete)")

    created = []
    updated = []
    failed = []

    for wc in config.workflow_configs:
        fqn = f"{client.parent}/releaseConfigs/{wc.release_config}"
        body = {**wc.body, "releaseConfig": fqn}
        resource_path = f"/workflowConfigs/{wc.id}"

        try:
            if client.dry_run:
                update_mask = _build_update_mask(
                    body,
                    (
                        "releaseConfig",
                        "cronSchedule",
                        "timeZone",
                        "disabled",
                    ),
                )
                client.upsert(
                    "workflowConfig",
                    wc.id,
                    "/workflowConfigs",
                    "workflowConfigId",
                    body,
                    update_mask=update_mask,
                )
                output.add_result(
                    StepResult("3/3", f"workflowConfig: {wc.id}", "dry_run", "Dry run")
                )
                continue

            existing = _get_existing_resource(client, resource_path)
            status = "success"
            if existing is None:
                print(f"  Creating workflowConfig: {wc.id}")
                client.post(
                    "/workflowConfigs", body, params={"workflowConfigId": wc.id}
                )
                print(f"  Created workflowConfig: {wc.id}")
                created.append(wc.id)
                detail = "Created"
            elif _workflow_config_requires_recreate(existing, body):
                print(
                    "  workflowConfig"
                    f" '{wc.id}' changes immutable-update field"
                    " (invocationConfig). Deleting and recreating..."
                )
                client.delete(resource_path)
                print(f"  Deleted workflowConfig: {wc.id}")
                client.post(
                    "/workflowConfigs", body, params={"workflowConfigId": wc.id}
                )
                print(f"  Recreated workflowConfig: {wc.id}")
                created.append(wc.id)
                detail = "Recreated"
            else:
                # invocationConfig is immutable via PATCH; only compare/patch the mutable fields.
                patch_fields = ("releaseConfig", "cronSchedule", "timeZone", "disabled")
                if all(_field_unchanged(existing, body, f) for f in patch_fields):
                    print(f"  workflowConfig '{wc.id}' is up to date")
                    status = "no_changes"
                    detail = "No changes"
                else:
                    print(f"  Updating workflowConfig: {wc.id}")
                    update_mask = _build_update_mask(body, patch_fields)
                    client.patch(
                        resource_path, body, params={"updateMask": update_mask}
                    )
                    print(f"  Updated workflowConfig: {wc.id}")
                    updated.append(wc.id)
                    detail = "Updated"

            output.add_result(
                StepResult("3/3", f"workflowConfig: {wc.id}", status, detail)
            )
        except ApiError as e:
            print(f"::error::Failed to deploy workflowConfig '{wc.id}': {e.message}")
            failed.append(wc.id)
            output.add_result(
                StepResult("3/3", f"workflowConfig: {wc.id}", "failed", "Failed")
            )

    # Sync-delete orphaned workflow configs
    deleted = []
    delete_failed = []

    if sync_delete and not client.dry_run:
        desired_ids = {wc.id for wc in config.workflow_configs}
        try:
            response = client.get("/workflowConfigs")
            existing = response.json().get("workflowConfigs", [])
            for entry in existing:
                existing_id = entry["name"].split("/")[-1]
                if existing_id not in desired_ids:
                    try:
                        print(f"  Deleting workflowConfig: {existing_id}")
                        client.delete(f"/workflowConfigs/{existing_id}")
                        print(f"  Deleted workflowConfig: {existing_id}")
                        deleted.append(existing_id)
                        output.add_result(
                            StepResult(
                                "3/3",
                                f"workflowConfig: {existing_id}",
                                "deleted",
                                "Deleted",
                            )
                        )
                    except ApiError as e:
                        print(
                            f"::error::Failed to delete workflowConfig"
                            f" '{existing_id}': {e.message}"
                        )
                        delete_failed.append(existing_id)
                        output.add_result(
                            StepResult(
                                "3/3",
                                f"workflowConfig: {existing_id}",
                                "failed",
                                "Delete failed",
                            )
                        )
        except ApiError as e:
            print(
                f"::warning::Failed to list workflow configs for sync-delete: {e.message}"
            )
    elif sync_delete and client.dry_run:
        print("  [dry-run] Would check for orphaned workflow configs to delete")

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


def main() -> None:
    # Required env vars
    config_file = os.environ.get("CONFIG_FILE", "")
    if not config_file:
        print(
            "::error::CONFIG_FILE is required to apply Dataform release / workflow configurations"
        )
        sys.exit(1)

    # Optional env vars with defaults
    workflow_settings = os.environ.get("WORKFLOW_SETTINGS", "workflow_settings.yaml")
    project_id = os.environ.get("PROJECT_ID", "") or None
    location = os.environ.get("LOCATION", "") or None
    do_compile = os.environ.get("DO_COMPILE", "false").lower() == "true"
    sync_delete = os.environ.get("SYNC_DELETE", "true").lower() == "true"
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    allow_empty_config = os.environ.get("ALLOW_EMPTY_CONFIG", "false").lower() == "true"
    # Resolve project_id, location, and default_dataset
    try:
        project_id, location, default_dataset = ConfigLoader.resolve_workflow_settings(
            workflow_settings, project_id, location
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"::error::{e}")
        sys.exit(1)

    # Load and validate config
    try:
        config = ConfigLoader.load(
            config_file,
            project_id=project_id,
            default_dataset=default_dataset,
        )
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        print(f"::error::Config error: {e}")
        sys.exit(1)

    if not config.release_configs and not allow_empty_config:
        print(
            "::error::release_configs is empty."
            " Set allow_empty_config: true if you intend to delete all configurations."
        )
        sys.exit(1)

    if not config.release_configs and config.workflow_configs:
        print(
            "::error::release_configs is empty but workflow_configs is not."
            " Both must be empty when allow_empty_config: true."
        )
        sys.exit(1)

    repository = config.repository

    location, original_location = normalize_location(location)
    if original_location is not None:
        print(
            "::warning::"
            f"Multi-region location '{original_location}' is not supported by Dataform. "
            f"Automatically converted to '{location}'."
        )

    # Initialize API client
    try:
        client = DataformApiClient(
            project_id=project_id,
            location=location,
            repository=repository,
            api_version="v1",
            dry_run=dry_run,
        )
    except Exception as e:
        print(
            f"::error::Authentication failed."
            f" Ensure google-github-actions/auth is configured: {e}"
        )
        sys.exit(1)

    output = GitHubOutput.from_env()

    # Banner
    print("══════════════════════════════════════════════════════")
    print(" Apply Dataform Release / Workflow Configurations")
    print("══════════════════════════════════════════════════════")
    print(f"  Project:    {project_id}")
    print(f"  Location:   {location}")
    print(f"  Repository: {repository}")
    print(f"  Compile:    {do_compile}")
    print(f"  Sync delete: {sync_delete}")
    print(f"  Dry run:    {dry_run}")
    print("──────────────────────────────────────────────────────")

    # Execute 3 steps
    deploy_release_configs(client, config, output)
    compile_release_configs(client, config, do_compile, output)
    deploy_workflow_configs(client, config, sync_delete, output)

    # Write outputs
    output.set_output("deployment_status", output.deployment_status)
    output.write_summary()

    # Final status
    print("")
    print("══════════════════════════════════════════════════════")
    if output.has_failure:
        print(" Deployment completed with errors")
        print("══════════════════════════════════════════════════════")
        sys.exit(1)
    else:
        print(" Deployment complete")
        print("══════════════════════════════════════════════════════")


if __name__ == "__main__":
    main()
