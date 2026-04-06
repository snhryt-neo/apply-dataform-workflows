from unittest.mock import MagicMock, patch

import pytest

from apply_dataform_workflows.client import ApiError, DataformApiClient, UpsertResult


@pytest.fixture
def mock_session():
    with patch("apply_dataform_workflows.client.google.auth.default") as mock_auth:
        mock_credentials = MagicMock()
        mock_auth.return_value = (mock_credentials, "test-project")
        with patch(
            "apply_dataform_workflows.client.AuthorizedSession"
        ) as mock_session_cls:
            session = MagicMock()
            mock_session_cls.return_value = session
            yield session


@pytest.fixture
def client(mock_session):
    return DataformApiClient(
        project_id="test-project",
        location="asia-northeast1",
        repository="test-repo",
    )


class TestDataformApiClientInit:
    def test_parent_property(self, client):
        assert (
            client.parent
            == "projects/test-project/locations/asia-northeast1/repositories/test-repo"
        )

    def test_base_url(self, client):
        assert client.base_url == "https://dataform.googleapis.com/v1"

    def test_custom_api_version(self, mock_session):
        c = DataformApiClient(
            project_id="p", location="l", repository="r", api_version="v1beta1"
        )
        assert c.base_url == "https://dataform.googleapis.com/v1beta1"


class TestApiCallMethods:
    def test_get_success(self, client, mock_session):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "test"}
        mock_session.get.return_value = mock_response

        response = client.get("/releaseConfigs/prod")
        mock_session.get.assert_called_once_with(
            f"{client.base_url}/{client.parent}/releaseConfigs/prod"
        )
        assert response.json() == {"name": "test"}

    def test_post_success(self, client, mock_session):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "created"}
        mock_session.post.return_value = mock_response

        client.post(
            "/releaseConfigs",
            body={"gitCommitish": "main"},
            params={"releaseConfigId": "prod"},
        )
        _, kwargs = mock_session.post.call_args
        assert kwargs["params"] == {"releaseConfigId": "prod"}
        assert kwargs["json"] == {"gitCommitish": "main"}

    def test_patch_success(self, client, mock_session):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_session.patch.return_value = mock_response

        client.patch("/releaseConfigs/prod", body={"gitCommitish": "main"})
        mock_session.patch.assert_called_once()

    def test_delete_success(self, client, mock_session):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_session.delete.return_value = mock_response

        client.delete("/workflowConfigs/old")
        mock_session.delete.assert_called_once()

    def test_get_raises_api_error_on_400(self, client, mock_session):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": {"message": "Bad request"}}
        mock_session.get.return_value = mock_response

        with pytest.raises(ApiError, match="Bad request"):
            client.get("/bad")

    def test_post_raises_api_error_on_500(self, client, mock_session):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": {"message": "Internal error"}}
        mock_session.post.return_value = mock_response

        with pytest.raises(ApiError) as exc_info:
            client.post("/fail", body={})
        assert exc_info.value.status_code == 500

    def test_error_falls_back_to_message_field(self, client, mock_session):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.json.return_value = {"message": "Forbidden"}
        mock_session.get.return_value = mock_response

        with pytest.raises(ApiError, match="Forbidden"):
            client.get("/forbidden")

    def test_error_falls_back_to_http_code(self, client, mock_session):
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.json.return_value = {}
        mock_session.get.return_value = mock_response

        with pytest.raises(ApiError, match="HTTP 502"):
            client.get("/bad-gateway")


class TestResourceExists:
    def test_returns_true_for_200(self, client, mock_session):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_session.get.return_value = mock_response

        assert client.resource_exists("/releaseConfigs/prod") is True

    def test_returns_false_for_404(self, client, mock_session):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {"error": {"message": "Not found"}}
        mock_session.get.return_value = mock_response

        assert client.resource_exists("/releaseConfigs/nonexistent") is False

    def test_raises_api_error_for_500(self, client, mock_session):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": {"message": "Server error"}}
        mock_session.get.return_value = mock_response

        with pytest.raises(ApiError, match="Server error"):
            client.resource_exists("/releaseConfigs/broken")


class TestUpsert:
    def test_upsert_updates_existing_resource(self, client, mock_session):
        get_response = MagicMock()
        get_response.status_code = 200
        patch_response = MagicMock()
        patch_response.status_code = 200
        patch_response.json.return_value = {"name": "updated"}
        mock_session.get.return_value = get_response
        mock_session.patch.return_value = patch_response

        result = client.upsert(
            "releaseConfig",
            "prod",
            "/releaseConfigs",
            "releaseConfigId",
            {"gitCommitish": "main"},
        )
        assert result == UpsertResult.UPDATED
        mock_session.patch.assert_called_once()

    def test_upsert_passes_update_mask_on_patch(self, client, mock_session):
        get_response = MagicMock()
        get_response.status_code = 200
        patch_response = MagicMock()
        patch_response.status_code = 200
        mock_session.get.return_value = get_response
        mock_session.patch.return_value = patch_response

        client.upsert(
            "releaseConfig",
            "prod",
            "/releaseConfigs",
            "releaseConfigId",
            {"gitCommitish": "main", "disabled": True},
            update_mask="gitCommitish,disabled",
        )

        _, kwargs = mock_session.patch.call_args
        assert kwargs["params"] == {"updateMask": "gitCommitish,disabled"}
        assert kwargs["json"] == {"gitCommitish": "main", "disabled": True}

    def test_upsert_creates_new_resource(self, client, mock_session):
        get_response = MagicMock()
        get_response.status_code = 404
        post_response = MagicMock()
        post_response.status_code = 200
        post_response.json.return_value = {"name": "created"}
        mock_session.get.return_value = get_response
        mock_session.post.return_value = post_response

        result = client.upsert(
            "releaseConfig",
            "prod",
            "/releaseConfigs",
            "releaseConfigId",
            {"gitCommitish": "main"},
            update_mask="gitCommitish,disabled",
        )
        assert result == UpsertResult.CREATED
        mock_session.post.assert_called_once()
        _, kwargs = mock_session.post.call_args
        assert kwargs["params"] == {"releaseConfigId": "prod"}

    def test_upsert_raises_on_patch_error(self, client, mock_session):
        get_response = MagicMock()
        get_response.status_code = 200
        patch_response = MagicMock()
        patch_response.status_code = 500
        patch_response.json.return_value = {"error": {"message": "Server error"}}
        mock_session.get.return_value = get_response
        mock_session.patch.return_value = patch_response

        with pytest.raises(ApiError, match="Server error"):
            client.upsert(
                "releaseConfig",
                "prod",
                "/releaseConfigs",
                "releaseConfigId",
                {},
            )

    def test_upsert_dry_run_skips_api_call(self, mock_session):
        dry_client = DataformApiClient(
            project_id="p", location="l", repository="r", dry_run=True
        )
        result = dry_client.upsert(
            "releaseConfig",
            "prod",
            "/releaseConfigs",
            "releaseConfigId",
            {"a": "b"},
        )
        assert result == UpsertResult.DRY_RUN
        mock_session.get.assert_not_called()
        mock_session.post.assert_not_called()
        mock_session.patch.assert_not_called()

    def test_dry_run_get_still_works(self, mock_session):
        dry_client = DataformApiClient(
            project_id="p", location="l", repository="r", dry_run=True
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}
        mock_session.get.return_value = mock_response

        response = dry_client.get("/test")
        assert response.status_code == 200
