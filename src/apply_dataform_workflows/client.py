from __future__ import annotations

import json as _json

import google.auth
from google.auth.transport.requests import AuthorizedSession


class ApiError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP {status_code}: {message}")


class UpsertResult:
    CREATED = "created"
    UPDATED = "updated"
    DRY_RUN = "dry_run"


class DataformApiClient:
    def __init__(
        self,
        project_id: str,
        location: str,
        repository: str,
        api_version: str = "v1",
        dry_run: bool = False,
    ):
        self._dry_run = dry_run
        self._base_url = f"https://dataform.googleapis.com/{api_version}"
        self._parent = (
            f"projects/{project_id}/locations/{location}/repositories/{repository}"
        )

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        self._session = AuthorizedSession(credentials)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def parent(self) -> str:
        return self._parent

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{self._parent}{path}"

    def _check_response(self, response) -> None:
        if response.status_code >= 400:
            try:
                data = response.json()
                message = (
                    data.get("error", {}).get("message")
                    or data.get("message")
                    or f"HTTP {response.status_code}"
                )
            except Exception:
                message = f"HTTP {response.status_code}"
            raise ApiError(response.status_code, message)

    def get(self, path: str):
        response = self._session.get(self._url(path))
        self._check_response(response)
        return response

    def post(self, path: str, body: dict, params: dict | None = None):
        url = self._url(path)
        response = self._session.post(url, json=body, params=params)
        self._check_response(response)
        return response

    def patch(self, path: str, body: dict, params: dict | None = None):
        url = self._url(path)
        response = self._session.patch(url, json=body, params=params)
        self._check_response(response)
        return response

    def delete(self, path: str):
        response = self._session.delete(self._url(path))
        self._check_response(response)
        return response

    def resource_exists(self, path: str) -> bool:
        response = self._session.get(self._url(path))
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        self._check_response(response)
        return False

    def upsert(
        self,
        resource_type: str,
        resource_id: str,
        parent_path: str,
        id_param: str,
        body: dict,
        update_mask: str | None = None,
    ) -> str:
        if self._dry_run:
            print(f"  [dry-run] Would upsert {resource_type}: {resource_id}")
            print(_json.dumps(body, indent=2))
            return UpsertResult.DRY_RUN

        resource_path = f"{parent_path}/{resource_id}"
        if self.resource_exists(resource_path):
            print(f"  Updating {resource_type}: {resource_id}")
            params = {"updateMask": update_mask} if update_mask else None
            self.patch(resource_path, body, params=params)
            print(f"  Updated {resource_type}: {resource_id}")
            return UpsertResult.UPDATED
        else:
            print(f"  Creating {resource_type}: {resource_id}")
            self.post(parent_path, body, params={id_param: resource_id})
            print(f"  Created {resource_type}: {resource_id}")
            return UpsertResult.CREATED
