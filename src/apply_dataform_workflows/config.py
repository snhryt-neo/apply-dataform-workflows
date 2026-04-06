from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_ALIASES: dict[str, str] = {
    "schedule": "cronSchedule",
    "timezone": "timeZone",
    "compile_override": "codeCompilationConfig",
    "options": "invocationConfig",
    "include_dependencies": "transitiveDependenciesIncluded",
    "include_dependents": "transitiveDependentsIncluded",
    "full_refresh": "fullyRefreshIncrementalTablesEnabled",
}

_PASSTHROUGH_KEYS = {"vars"}
_MULTIREGION_LOCATION_MAP = {
    "us": "us-central1",
    "eu": "europe-west1",
}


def _snake_to_camel(key: str) -> str:
    parts = key.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def _normalize_key(key: str) -> str:
    if key in _ALIASES:
        return _ALIASES[key]
    return _snake_to_camel(key)


def _convert_keys_deep(obj: Any) -> Any:
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            api_key = _normalize_key(key)
            if api_key in _PASSTHROUGH_KEYS:
                result[api_key] = value
            else:
                result[api_key] = _convert_keys_deep(value)
        return result
    if isinstance(obj, list):
        return [_convert_keys_deep(item) for item in obj]
    return obj


def normalize_location(location: str) -> tuple[str, str | None]:
    normalized_key = location.strip().lower()
    converted_location = _MULTIREGION_LOCATION_MAP.get(normalized_key)
    if converted_location is not None:
        return converted_location, location
    return location, None


@dataclass
class ReleaseConfig:
    id: str
    body: dict


@dataclass
class WorkflowConfig:
    id: str
    release_config: str
    body: dict


@dataclass
class DeployConfig:
    repository: str
    release_configs: list[ReleaseConfig]
    workflow_configs: list[WorkflowConfig]


class ConfigLoader:
    @staticmethod
    def load(config_path: str | Path) -> DeployConfig:
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path) as f:
            data = json.load(f)
        data = _convert_keys_deep(data)

        if "repository" not in data:
            raise ValueError("Config is missing required field 'repository'")
        repository = data["repository"]

        release_configs = []
        for i, rc in enumerate(data.get("releaseConfigs", [])):
            if "id" not in rc:
                raise ValueError(f"release_configs[{i}] is missing required field 'id'")
            rc_body = {k: v for k, v in rc.items() if k != "id"}
            ConfigLoader._set_git_commitish(rc_body, f"release_configs[{i}]")
            rc_body.setdefault("disabled", False)
            release_configs.append(ReleaseConfig(id=rc["id"], body=rc_body))

        if not release_configs:
            raise ValueError("release_configs must not be empty")

        workflow_configs = []
        for i, wc in enumerate(data.get("workflowConfigs", [])):
            if "id" not in wc:
                raise ValueError(
                    f"workflow_configs[{i}] is missing required field 'id'"
                )
            if "releaseConfig" not in wc:
                raise ValueError(
                    f"workflow_configs[{i}] is missing required field 'release_config'"
                )
            wc_body = {k: v for k, v in wc.items() if k not in ("id", "releaseConfig")}
            ConfigLoader._merge_workflow_targets(wc_body, f"workflow_configs[{i}]")
            wc_body.setdefault("disabled", False)
            workflow_configs.append(
                WorkflowConfig(
                    id=wc["id"], release_config=wc["releaseConfig"], body=wc_body
                )
            )

        # Validate: duplicate release config IDs
        rc_ids = [rc.id for rc in release_configs]
        rc_dupes = [rid for rid in set(rc_ids) if rc_ids.count(rid) > 1]
        if rc_dupes:
            raise ValueError(f"Duplicate release_configs id: '{rc_dupes[0]}'")

        # Validate: duplicate workflow config IDs
        wc_ids = [wc.id for wc in workflow_configs]
        wc_dupes = [wid for wid in set(wc_ids) if wc_ids.count(wid) > 1]
        if wc_dupes:
            raise ValueError(f"Duplicate workflow_configs id: '{wc_dupes[0]}'")

        return DeployConfig(
            repository=repository,
            release_configs=release_configs,
            workflow_configs=workflow_configs,
        )

    @staticmethod
    def resolve_workflow_settings(
        workflow_settings_path: str | Path,
        project_id: str | None,
        location: str | None,
    ) -> tuple[str, str]:
        if project_id and location:
            return project_id, location

        path = Path(workflow_settings_path)
        if not path.exists():
            raise FileNotFoundError(
                f"workflow_settings.yaml not found at '{path}' "
                "and project_id/location not fully provided"
            )

        settings = ConfigLoader._read_yaml_field(path)

        if not project_id:
            project_id = settings.get("defaultProject")
            if not project_id:
                raise ValueError(
                    f"defaultProject not found in {path} and project_id not provided"
                )

        if not location:
            location = settings.get("defaultLocation")
            if not location:
                raise ValueError(
                    f"defaultLocation not found in {path} and location not provided"
                )

        return project_id, location

    @staticmethod
    def _read_yaml_field(path: Path) -> dict[str, str]:
        result = {}
        with open(path) as f:
            for line in f:
                line = line.strip()
                if ":" in line:
                    key, _, value = line.partition(":")
                    value = value.strip().strip("'\"")
                    result[key.strip()] = value
        return result

    @staticmethod
    def _set_git_commitish(body: dict[str, Any], path: str) -> None:
        git_ref = body.pop("gitRef", None)
        if not isinstance(git_ref, str) or not git_ref:
            raise ValueError(f"{path} is missing required field 'git_ref'")
        body["gitCommitish"] = git_ref

    @staticmethod
    def _merge_workflow_targets(body: dict[str, Any], path: str) -> None:
        targets = body.pop("targets", None)
        if not isinstance(targets, dict):
            raise ValueError(f"{path} is missing required field 'targets'")

        invocation_config = body.get("invocationConfig")
        if invocation_config is None:
            invocation_config = {}
            body["invocationConfig"] = invocation_config
        elif not isinstance(invocation_config, dict):
            raise ValueError(f"{path}.options must be an object")

        has_tags = bool(targets.get("tags"))
        has_actions = bool(targets.get("actions"))
        is_all = targets.get("isAll") is True

        if sum((has_tags, has_actions, is_all)) != 1:
            raise ValueError(
                f"{path}.targets must contain exactly one of 'tags', 'actions', or 'is_all: true'"
            )

        if has_tags:
            invocation_config["includedTags"] = targets["tags"]
            return

        if has_actions:
            invocation_config["includedTargets"] = [
                {"name": action_name} for action_name in targets["actions"]
            ]
            return

        invocation_config.pop("includedTags", None)
        invocation_config.pop("includedTargets", None)
