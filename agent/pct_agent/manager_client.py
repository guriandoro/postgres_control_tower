"""Tiny wrapper around ``httpx.AsyncClient`` for collector → manager POSTs.

Centralizes URL construction, bearer auth, timeouts, and 401 logging so the
collectors stay focused on *what* to ship rather than *how*.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0


class ManagerClient:
    def __init__(
        self,
        manager_url: str,
        agent_token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base = manager_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {agent_token}"},
        )

    async def __aenter__(self) -> "ManagerClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def post(self, path: str, json: dict[str, Any]) -> httpx.Response:
        url = f"{self._base}{path}"
        response = await self._client.post(url, json=json)
        if response.status_code == 401:
            logger.error(
                "Manager rejected agent token at %s. Re-run `pct-agent register`.",
                path,
            )
        else:
            response.raise_for_status()
        return response

    async def post_file(
        self,
        path: str,
        *,
        file_path: str,
        filename: str,
        content_type: str = "application/gzip",
        extra_form: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Upload a single file as multipart/form-data.

        Used by the runner to ship pt-stalk bundles to the manager. The
        file is streamed from disk by httpx so we never hold the whole
        thing in memory. ``timeout`` is widened separately because
        100+ MiB uploads on a slow link can blow past the default 15s.
        """
        url = f"{self._base}{path}"
        data = dict(extra_form or {})
        data["filename"] = filename
        with open(file_path, "rb") as fh:
            files = {"file": (filename, fh, content_type)}
            kwargs: dict[str, Any] = {"files": files, "data": data}
            if timeout is not None:
                kwargs["timeout"] = timeout
            response = await self._client.post(url, **kwargs)
        if response.status_code == 401:
            logger.error(
                "Manager rejected agent token at %s. Re-run `pct-agent register`.",
                path,
            )
        else:
            response.raise_for_status()
        return response

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """GET wrapper. ``timeout`` overrides the client default for this call
        (used by the runner's long-poll, which legitimately blocks for ~25s)."""
        url = f"{self._base}{path}"
        kwargs: dict[str, Any] = {}
        if params is not None:
            kwargs["params"] = params
        if timeout is not None:
            kwargs["timeout"] = timeout
        response = await self._client.get(url, **kwargs)
        if response.status_code == 401:
            logger.error(
                "Manager rejected agent token at %s. Re-run `pct-agent register`.",
                path,
            )
            return response
        if response.status_code != 204:
            response.raise_for_status()
        return response
