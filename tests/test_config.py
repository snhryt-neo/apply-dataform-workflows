import json

import pytest

from apply_dataform_workflows.config import (
    ConfigLoader,
    DeployConfig,
    ReleaseConfig,
    WorkflowConfig,
    _convert_keys_deep,
    _normalize_key,
    _snake_to_camel,
    normalize_location,
)


class TestKeyConversion:
    def test_snake_to_camel_converts_basic_cases(self):
        assert _snake_to_camel("git_commitish") == "gitCommitish"
        assert _snake_to_camel("commit_sha") == "commitSha"

    def test_snake_to_camel_keeps_single_word_and_schema(self):
        assert _snake_to_camel("repository") == "repository"
        assert _snake_to_camel("timezone") == "timezone"
        assert _snake_to_camel("$schema") == "$schema"

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("schedule", "cronSchedule"),
            ("timezone", "timeZone"),
            ("compile_override", "codeCompilationConfig"),
            ("options", "invocationConfig"),
            ("include_dependencies", "transitiveDependenciesIncluded"),
            ("include_dependents", "transitiveDependentsIncluded"),
            ("full_refresh", "fullyRefreshIncrementalTablesEnabled"),
            ("service_account", "serviceAccount"),
        ],
    )
    def test_normalize_key_handles_aliases_and_mechanical_conversion(
        self, key, expected
    ):
        assert _normalize_key(key) == expected

    def test_convert_keys_deep_converts_nested_objects_and_lists(self):
        data = {
            "release_configs": [
                {
                    "git_ref": "main",
                    "compile_override": {
                        "default_schema": "analytics",
                    },
                }
            ],
            "workflow_configs": [
                {
                    "options": {
                        "service_account": "runner@example.com",
                    }
                }
            ],
        }

        converted = _convert_keys_deep(data)

        assert converted["releaseConfigs"][0]["gitRef"] == "main"
        assert (
            converted["releaseConfigs"][0]["codeCompilationConfig"]["defaultSchema"]
            == "analytics"
        )
        assert (
            converted["workflowConfigs"][0]["invocationConfig"]["serviceAccount"]
            == "runner@example.com"
        )

    def test_convert_keys_deep_preserves_vars_children(self):
        data = {
            "release_configs": [
                {"compile_override": {"vars": {"env_name": "prod", "another_key": "x"}}}
            ]
        }

        converted = _convert_keys_deep(data)

        assert converted["releaseConfigs"][0]["codeCompilationConfig"]["vars"] == {
            "env_name": "prod",
            "another_key": "x",
        }


class TestConfigLoaderLoad:
    def test_load_simple_config(self, fixtures_dir):
        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        assert isinstance(config, DeployConfig)
        assert len(config.release_configs) == 1
        assert len(config.workflow_configs) == 1

    def test_load_release_config_fields(self, fixtures_dir):
        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        rc = config.release_configs[0]
        assert isinstance(rc, ReleaseConfig)
        assert rc.id == "production"
        assert rc.body["gitCommitish"] == "main"
        assert "id" not in rc.body

    def test_load_workflow_config_fields(self, fixtures_dir):
        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        wc = config.workflow_configs[0]
        assert isinstance(wc, WorkflowConfig)
        assert wc.id == "daily-run"
        assert wc.release_config == "production"
        assert wc.body["invocationConfig"] == {}
        assert "id" not in wc.body
        assert "releaseConfig" not in wc.body

    def test_load_advanced_config(self, fixtures_dir):
        config = ConfigLoader.load(fixtures_dir / "config_advanced.json")
        assert len(config.release_configs) == 2
        assert len(config.workflow_configs) == 2

    def test_load_config_without_workflow_configs(self, fixtures_dir):
        config = ConfigLoader.load(fixtures_dir / "config_no_workflow.json")
        assert config.workflow_configs == []

    def test_load_repository_field(self, fixtures_dir):
        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        assert config.repository == "my-dataform-repo"

    def test_load_repository_stripped_from_raw_data(self, fixtures_dir):
        config = ConfigLoader.load(fixtures_dir / "config_simple.json")
        for rc in config.release_configs:
            assert "repository" not in rc.body
        for wc in config.workflow_configs:
            assert "repository" not in wc.body

    def test_load_accepts_str_path(self, fixtures_dir):
        config = ConfigLoader.load(str(fixtures_dir / "config_simple.json"))
        assert isinstance(config, DeployConfig)

    def test_load_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ConfigLoader.load(tmp_path / "nonexistent.json")

    def test_load_invalid_json_raises(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json")
        with pytest.raises(json.JSONDecodeError):
            ConfigLoader.load(bad_file)

    def test_load_preserves_vars_child_keys(self, fixtures_dir):
        config = ConfigLoader.load(fixtures_dir / "config_advanced.json")
        rc = next(rc for rc in config.release_configs if rc.id == "development")
        assert rc.body["codeCompilationConfig"]["vars"]["env"] == "development"

    def test_load_preserves_target_name_key(self, fixtures_dir):
        config = ConfigLoader.load(fixtures_dir / "config_advanced.json")
        wc = next(wc for wc in config.workflow_configs if wc.id == "mart-daily")
        assert wc.body["invocationConfig"]["includedTargets"][0]["name"] == "users"

    def test_load_converts_full_round_trip_to_api_shape(self, fixtures_dir):
        config = ConfigLoader.load(fixtures_dir / "config_advanced.json")
        rc = next(rc for rc in config.release_configs if rc.id == "development")
        wc = next(wc for wc in config.workflow_configs if wc.id == "staging-refresh")

        assert rc.body["gitCommitish"] == "develop"
        assert rc.body["codeCompilationConfig"]["defaultDatabase"] == "my-project-dev"
        assert wc.body["invocationConfig"]["includedTags"] == [
            "staging",
            "experiment",
        ]
        assert (
            wc.body["invocationConfig"]["fullyRefreshIncrementalTablesEnabled"] is True
        )
        assert (
            wc.body["invocationConfig"]["serviceAccount"]
            == "workflow-sa@test-project.iam.gserviceaccount.com"
        )

    def test_load_release_config_accepts_any_git_ref_string(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            """
            {
              "repository": "repo",
              "release_configs": [
                {
                  "id": "tagged",
                  "git_ref": "v1.2.3"
                },
                {
                  "id": "pinned",
                  "git_ref": "abc1234"
                }
              ]
            }
            """
        )

        config = ConfigLoader.load(config_file)

        assert config.release_configs[0].body["gitCommitish"] == "v1.2.3"
        assert config.release_configs[1].body["gitCommitish"] == "abc1234"

    @pytest.mark.parametrize("disabled", [True, False])
    def test_load_release_config_preserves_disabled(self, tmp_path, disabled):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [
                        {
                            "id": "prod",
                            "git_ref": "main",
                            "disabled": disabled,
                        }
                    ],
                }
            )
        )

        config = ConfigLoader.load(config_file)

        assert config.release_configs[0].body["disabled"] is disabled

    def test_load_release_config_defaults_disabled_to_false_when_absent(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [{"id": "prod", "git_ref": "main"}],
                }
            )
        )

        config = ConfigLoader.load(config_file)

        assert config.release_configs[0].body["disabled"] is False

    @pytest.mark.parametrize(
        ("targets", "expected"),
        [
            ({"tags": ["daily"]}, {"includedTags": ["daily"]}),
            (
                {"actions": ["users", "sessions"]},
                {"includedTargets": [{"name": "users"}, {"name": "sessions"}]},
            ),
            ({"is_all": True}, {}),
        ],
    )
    def test_load_workflow_targets_forms(self, tmp_path, targets, expected):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [{"id": "prod", "git_ref": "main"}],
                    "workflow_configs": [
                        {
                            "id": "wc1",
                            "release_config": "prod",
                            "targets": targets,
                            "options": {"include_dependencies": True},
                        }
                    ],
                }
            )
        )

        # No project_id / default_dataset → name-only fallback
        config = ConfigLoader.load(config_file)

        assert config.workflow_configs[0].body["invocationConfig"] == {
            **expected,
            "transitiveDependenciesIncluded": True,
        }

    def test_load_actions_targets_fully_qualified_when_project_and_dataset_given(
        self, tmp_path
    ):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [{"id": "prod", "git_ref": "main"}],
                    "workflow_configs": [
                        {
                            "id": "wc1",
                            "release_config": "prod",
                            "targets": {"actions": ["users", "sessions"]},
                        }
                    ],
                }
            )
        )

        config = ConfigLoader.load(
            config_file, project_id="my-project", default_dataset="my_dataset"
        )

        assert config.workflow_configs[0].body["invocationConfig"][
            "includedTargets"
        ] == [
            {"database": "my-project", "schema": "my_dataset", "name": "users"},
            {"database": "my-project", "schema": "my_dataset", "name": "sessions"},
        ]

    def test_load_actions_targets_dataset_only_when_no_project(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [{"id": "prod", "git_ref": "main"}],
                    "workflow_configs": [
                        {
                            "id": "wc1",
                            "release_config": "prod",
                            "targets": {"actions": ["users"]},
                        }
                    ],
                }
            )
        )

        config = ConfigLoader.load(
            config_file, project_id=None, default_dataset="my_dataset"
        )

        assert config.workflow_configs[0].body["invocationConfig"][
            "includedTargets"
        ] == [{"schema": "my_dataset", "name": "users"}]

    def test_load_actions_targets_object_format_used_as_is(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [{"id": "prod", "git_ref": "main"}],
                    "workflow_configs": [
                        {
                            "id": "wc1",
                            "release_config": "prod",
                            "targets": {
                                "actions": [
                                    {
                                        "name": "users",
                                        "database": "explicit-project",
                                        "schema": "explicit_ds",
                                    }
                                ]
                            },
                        }
                    ],
                }
            )
        )

        config = ConfigLoader.load(
            config_file, project_id="default-project", default_dataset="default_ds"
        )

        assert config.workflow_configs[0].body["invocationConfig"][
            "includedTargets"
        ] == [
            {
                "name": "users",
                "database": "explicit-project",
                "schema": "explicit_ds",
            }
        ]

    def test_load_actions_targets_mixed_string_and_object(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [{"id": "prod", "git_ref": "main"}],
                    "workflow_configs": [
                        {
                            "id": "wc1",
                            "release_config": "prod",
                            "targets": {
                                "actions": [
                                    {
                                        "name": "users",
                                        "database": "explicit-project",
                                        "schema": "explicit_ds",
                                    },
                                    "sessions",
                                ]
                            },
                        }
                    ],
                }
            )
        )

        config = ConfigLoader.load(
            config_file, project_id="default-project", default_dataset="default_ds"
        )

        assert config.workflow_configs[0].body["invocationConfig"][
            "includedTargets"
        ] == [
            {
                "name": "users",
                "database": "explicit-project",
                "schema": "explicit_ds",
            },
            {
                "database": "default-project",
                "schema": "default_ds",
                "name": "sessions",
            },
        ]

    def test_load_actions_targets_object_missing_database_and_schema_uses_defaults(
        self, tmp_path
    ):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [{"id": "prod", "git_ref": "main"}],
                    "workflow_configs": [
                        {
                            "id": "wc1",
                            "release_config": "prod",
                            "targets": {
                                "actions": [
                                    {
                                        "name": "users",
                                    }
                                ]
                            },
                        }
                    ],
                }
            )
        )

        config = ConfigLoader.load(
            config_file, project_id="default-project", default_dataset="default_ds"
        )

        assert config.workflow_configs[0].body["invocationConfig"][
            "includedTargets"
        ] == [
            {
                "name": "users",
                "database": "default-project",
                "schema": "default_ds",
            }
        ]

    def test_load_actions_targets_object_null_database_and_schema_uses_defaults(
        self, tmp_path
    ):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [{"id": "prod", "git_ref": "main"}],
                    "workflow_configs": [
                        {
                            "id": "wc1",
                            "release_config": "prod",
                            "targets": {
                                "actions": [
                                    {
                                        "name": "users",
                                        "database": None,
                                        "schema": None,
                                    }
                                ]
                            },
                        }
                    ],
                }
            )
        )

        config = ConfigLoader.load(
            config_file, project_id="default-project", default_dataset="default_ds"
        )

        assert config.workflow_configs[0].body["invocationConfig"][
            "includedTargets"
        ] == [
            {
                "name": "users",
                "database": "default-project",
                "schema": "default_ds",
            }
        ]

    def test_load_actions_targets_name_only_when_no_project_or_dataset(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [{"id": "prod", "git_ref": "main"}],
                    "workflow_configs": [
                        {
                            "id": "wc1",
                            "release_config": "prod",
                            "targets": {"actions": ["users"]},
                        }
                    ],
                }
            )
        )

        config = ConfigLoader.load(config_file)

        assert config.workflow_configs[0].body["invocationConfig"][
            "includedTargets"
        ] == [{"name": "users"}]

    def test_load_tags_target_unaffected_by_project_and_dataset(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [{"id": "prod", "git_ref": "main"}],
                    "workflow_configs": [
                        {
                            "id": "wc1",
                            "release_config": "prod",
                            "targets": {"tags": ["daily"]},
                        }
                    ],
                }
            )
        )

        config = ConfigLoader.load(
            config_file, project_id="my-project", default_dataset="my_dataset"
        )

        ic = config.workflow_configs[0].body["invocationConfig"]
        assert ic == {"includedTags": ["daily"]}

    def test_load_is_all_target_unaffected_by_project_and_dataset(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [{"id": "prod", "git_ref": "main"}],
                    "workflow_configs": [
                        {
                            "id": "wc1",
                            "release_config": "prod",
                            "targets": {"is_all": True},
                        }
                    ],
                }
            )
        )

        config = ConfigLoader.load(
            config_file, project_id="my-project", default_dataset="my_dataset"
        )

        ic = config.workflow_configs[0].body["invocationConfig"]
        assert "includedTargets" not in ic
        assert "includedTags" not in ic

    @pytest.mark.parametrize("disabled", [True, False])
    def test_load_workflow_config_preserves_disabled(self, tmp_path, disabled):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [{"id": "prod", "git_ref": "main"}],
                    "workflow_configs": [
                        {
                            "id": "daily",
                            "release_config": "prod",
                            "disabled": disabled,
                            "targets": {"tags": ["daily"]},
                        }
                    ],
                }
            )
        )

        config = ConfigLoader.load(config_file)

        assert config.workflow_configs[0].body["disabled"] is disabled

    def test_load_workflow_config_defaults_disabled_to_false_when_absent(
        self, tmp_path
    ):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "repository": "repo",
                    "release_configs": [{"id": "prod", "git_ref": "main"}],
                    "workflow_configs": [
                        {
                            "id": "daily",
                            "release_config": "prod",
                            "targets": {"tags": ["daily"]},
                        }
                    ],
                }
            )
        )

        config = ConfigLoader.load(config_file)

        assert config.workflow_configs[0].body["disabled"] is False


class TestConfigLoaderValidation:
    def test_missing_repository_raises(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text('{"release_configs": [{"id": "x", "git_ref": "main"}]}')
        with pytest.raises(ValueError, match="missing required field 'repository'"):
            ConfigLoader.load(bad_file)

    def test_duplicate_release_config_ids_raises(self, fixtures_dir):
        with pytest.raises(
            ValueError, match="Duplicate release_configs id: 'production'"
        ):
            ConfigLoader.load(fixtures_dir / "config_duplicate_rc.json")

    def test_duplicate_workflow_config_ids_raises(self, fixtures_dir):
        with pytest.raises(
            ValueError, match="Duplicate workflow_configs id: 'daily-run'"
        ):
            ConfigLoader.load(fixtures_dir / "config_duplicate_wc.json")

    def test_release_config_missing_id_raises(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(
            '{"repository": "repo", "release_configs": [{"git_ref": "main"}], "workflow_configs": []}'
        )
        with pytest.raises(
            ValueError, match="release_configs\\[0\\] is missing required field 'id'"
        ):
            ConfigLoader.load(bad_file)

    def test_workflow_config_missing_id_raises(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(
            '{"repository": "repo", "release_configs": [{"id": "prod", "git_ref": "main"}], "workflow_configs": [{"release_config": "prod", "targets": {"is_all": true}}]}'
        )
        with pytest.raises(
            ValueError, match="workflow_configs\\[0\\] is missing required field 'id'"
        ):
            ConfigLoader.load(bad_file)

    def test_workflow_config_missing_release_config_raises(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(
            '{"repository": "repo", "release_configs": [{"id": "prod", "git_ref": "main"}], "workflow_configs": [{"id": "wc1", "targets": {"is_all": true}}]}'
        )
        with pytest.raises(
            ValueError,
            match="workflow_configs\\[0\\] is missing required field 'release_config'",
        ):
            ConfigLoader.load(bad_file)

    def test_release_config_missing_git_ref_raises(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(
            '{"repository": "repo", "release_configs": [{"id": "prod"}], "workflow_configs": []}'
        )
        with pytest.raises(
            ValueError,
            match="release_configs\\[0\\] is missing required field 'git_ref'",
        ):
            ConfigLoader.load(bad_file)

    def test_release_config_invalid_git_ref_raises(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(
            '{"repository": "repo", "release_configs": [{"id": "prod", "git_ref": {} }], "workflow_configs": []}'
        )
        with pytest.raises(
            ValueError,
            match="release_configs\\[0\\] is missing required field 'git_ref'",
        ):
            ConfigLoader.load(bad_file)

    def test_workflow_config_missing_targets_raises(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(
            '{"repository": "repo", "release_configs": [{"id": "prod", "git_ref": "main"}], "workflow_configs": [{"id": "wc1", "release_config": "prod"}]}'
        )
        with pytest.raises(
            ValueError,
            match="workflow_configs\\[0\\] is missing required field 'targets'",
        ):
            ConfigLoader.load(bad_file)

    def test_workflow_config_invalid_targets_raises(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(
            '{"repository": "repo", "release_configs": [{"id": "prod", "git_ref": "main"}], "workflow_configs": [{"id": "wc1", "release_config": "prod", "targets": {"tags": ["daily"], "is_all": true}}]}'
        )
        with pytest.raises(
            ValueError,
            match="workflow_configs\\[0\\]\\.targets must contain exactly one of 'tags', 'actions', or 'is_all: true'",
        ):
            ConfigLoader.load(bad_file)

    def test_valid_config_passes_validation(self, fixtures_dir):
        config = ConfigLoader.load(fixtures_dir / "config_advanced.json")
        assert len(config.release_configs) == 2


class TestNormalizeLocation:
    @pytest.mark.parametrize(
        ("location", "expected"),
        [
            ("US", ("us-central1", "US")),
            ("us", ("us-central1", "us")),
            (" US ", ("us-central1", " US ")),
            ("EU", ("europe-west1", "EU")),
            ("eu", ("europe-west1", "eu")),
            ("asia-northeast1", ("asia-northeast1", None)),
            ("us-central1", ("us-central1", None)),
        ],
    )
    def test_normalize_location(self, location, expected):
        assert normalize_location(location) == expected


class TestResolveWorkflowSettings:
    def test_reads_project_and_location_from_yaml(self, fixtures_dir):
        project_id, location, default_dataset = ConfigLoader.resolve_workflow_settings(
            fixtures_dir / "workflow_settings.yaml", None, None
        )
        assert project_id == "test-project"
        assert location == "asia-northeast1"
        assert default_dataset is None

    def test_explicit_values_override_yaml(self, fixtures_dir):
        project_id, location, default_dataset = ConfigLoader.resolve_workflow_settings(
            fixtures_dir / "workflow_settings.yaml", "override-project", "us-central1"
        )
        assert project_id == "override-project"
        assert location == "us-central1"
        assert default_dataset is None

    def test_partial_override(self, fixtures_dir):
        project_id, location, default_dataset = ConfigLoader.resolve_workflow_settings(
            fixtures_dir / "workflow_settings.yaml", "override-project", None
        )
        assert project_id == "override-project"
        assert location == "asia-northeast1"
        assert default_dataset is None

    def test_missing_yaml_without_overrides_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ConfigLoader.resolve_workflow_settings(
                tmp_path / "nonexistent.yaml", None, None
            )

    def test_missing_yaml_with_full_overrides_succeeds(self, tmp_path):
        project_id, location, default_dataset = ConfigLoader.resolve_workflow_settings(
            tmp_path / "nonexistent.yaml", "my-project", "us-east1"
        )
        assert project_id == "my-project"
        assert location == "us-east1"
        assert default_dataset is None

    def test_missing_key_in_yaml_raises(self, tmp_path):
        yaml_file = tmp_path / "workflow_settings.yaml"
        yaml_file.write_text("defaultProject: my-project\n")
        with pytest.raises(ValueError, match="defaultLocation"):
            ConfigLoader.resolve_workflow_settings(yaml_file, None, None)

    def test_strips_quotes_from_yaml_values(self, tmp_path):
        yaml_file = tmp_path / "workflow_settings.yaml"
        yaml_file.write_text(
            "defaultProject: 'quoted-project'\ndefaultLocation: \"double-quoted\"\n"
        )
        project_id, location, default_dataset = ConfigLoader.resolve_workflow_settings(
            yaml_file, None, None
        )
        assert project_id == "quoted-project"
        assert location == "double-quoted"
        assert default_dataset is None

    def test_reads_default_dataset_from_yaml(self, tmp_path):
        yaml_file = tmp_path / "workflow_settings.yaml"
        yaml_file.write_text(
            "defaultProject: my-project\n"
            "defaultLocation: us-central1\n"
            "defaultDataset: my_dataset\n"
        )
        project_id, location, default_dataset = ConfigLoader.resolve_workflow_settings(
            yaml_file, None, None
        )
        assert project_id == "my-project"
        assert location == "us-central1"
        assert default_dataset == "my_dataset"

    def test_default_dataset_not_returned_when_full_overrides_provided(self, tmp_path):
        yaml_file = tmp_path / "workflow_settings.yaml"
        yaml_file.write_text(
            "defaultProject: my-project\n"
            "defaultLocation: us-central1\n"
            "defaultDataset: my_dataset\n"
        )
        _, _, default_dataset = ConfigLoader.resolve_workflow_settings(
            yaml_file, "override-project", "us-east1"
        )
        # When both project_id and location are provided, YAML is not read
        assert default_dataset is None
