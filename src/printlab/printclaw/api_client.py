"""Async HTTP client wrapping the PrintLab REST API."""

from __future__ import annotations

from typing import Optional

import httpx


class PrintLabAPIClient:
    """Thin async wrapper around the PrintLab REST API."""

    def __init__(self, base_url: str = "http://localhost:8000", api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=60.0,
            )
        return self._client

    async def health(self) -> dict:
        c = await self._ensure_client()
        resp = await c.get("/api/health")
        resp.raise_for_status()
        return resp.json()

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        c = await self._ensure_client()
        resp = await c.post("/api/search", json={"query": query, "limit": limit})
        resp.raise_for_status()
        return resp.json().get("models", [])

    async def status(self) -> dict:
        c = await self._ensure_client()
        resp = await c.get("/api/status")
        resp.raise_for_status()
        return resp.json()

    async def print_model(
        self,
        model_id: Optional[str] = None,
        model_url: Optional[str] = None,
        filename: Optional[str] = None,
        ams_mapping: Optional[list[int]] = None,
    ) -> dict:
        c = await self._ensure_client()
        payload: dict = {}
        if model_id:
            payload["model_id"] = model_id
        if model_url:
            payload["model_url"] = model_url
        if filename:
            payload["filename"] = filename
        if ams_mapping is not None:
            payload["ams_mapping"] = ams_mapping
        resp = await c.post("/api/print", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def trays(self) -> list[dict]:
        c = await self._ensure_client()
        resp = await c.get("/api/trays")
        resp.raise_for_status()
        return resp.json().get("trays", [])

    async def pause(self) -> dict:
        c = await self._ensure_client()
        resp = await c.post("/api/pause")
        resp.raise_for_status()
        return resp.json()

    async def resume(self) -> dict:
        c = await self._ensure_client()
        resp = await c.post("/api/resume")
        resp.raise_for_status()
        return resp.json()

    async def stop(self) -> dict:
        c = await self._ensure_client()
        resp = await c.post("/api/stop")
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
