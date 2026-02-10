"""MakerWorld search provider (Bambu Lab's model repository)."""

from __future__ import annotations

import re
import json
import httpx
from typing import Optional

from .base import SearchProvider, Model


class MakerWorldSearch(SearchProvider):
    """Search provider for MakerWorld (makerworld.com)."""

    name = "MakerWorld"
    BASE_URL = "https://makerworld.com"

    def __init__(self):
        self._access_token: Optional[str] = None
        self._logged_in = False

        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
            timeout=30.0,
            follow_redirects=True,
        )

        # Cache for instance IDs (model_id -> instance_id)
        self._instance_cache: dict[str, int] = {}

    def _apply_token(self, token: str) -> None:
        """Store token and set Authorization header."""
        self._access_token = token
        self._logged_in = True
        self.client.headers["Authorization"] = f"Bearer {token}"

    def _get_local_token(self) -> bool:
        """Extract auth token from local BambuStudio/OrcaSlicer installation."""
        try:
            from ..bridge import extract_token

            token = extract_token()
            if token:
                self._apply_token(token)
                return True

            print("MakerWorld: Could not get token. Log into BambuStudio or OrcaSlicer and try again.")
            return False
        except Exception as e:
            print(f"MakerWorld: Token extraction error: {e}")
            print("MakerWorld: Log into BambuStudio or OrcaSlicer and try again.")
            return False

    async def search(self, query: str, limit: int = 10) -> list[Model]:
        """Search MakerWorld for models via web scraping."""
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/en/search/models",
                params={"keyword": query},
            )
            response.raise_for_status()

            # Extract JSON data from Next.js __NEXT_DATA__ script
            match = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                response.text
            )
            if not match:
                print("MakerWorld: Could not find __NEXT_DATA__")
                return []

            data = json.loads(match.group(1))
            designs = data.get("props", {}).get("pageProps", {}).get("designs", [])

            models = []
            for item in designs[:limit]:
                if not isinstance(item, dict):
                    continue

                design_id = item.get("id", "")
                creator = item.get("designCreator", {})

                model = Model(
                    id=str(design_id),
                    name=item.get("title", "Unknown"),
                    url=f"{self.BASE_URL}/en/models/{design_id}",  # Use numeric ID
                    thumbnail=item.get("cover"),
                    author=creator.get("name", "Unknown"),
                    provider=self.name,
                    downloads=item.get("downloadCount", 0),
                    likes=item.get("likeCount", 0),
                )
                models.append(model)

            return models

        except Exception as e:
            print(f"MakerWorld search error: {e}")
            return []

    async def get_download_url(self, model: Model) -> Optional[str]:
        """Get download URL for a MakerWorld model."""
        # Lazy token extraction on first download attempt
        if not self._logged_in:
            if not self._get_local_token():
                return None

        try:
            # Get instance ID from model page
            instance_id = await self._get_instance_id(model)
            if not instance_id:
                print(f"MakerWorld: No instance found for {model.name}")
                return None

            # Get signed download URL
            download_url = await self._fetch_download_url(instance_id, model)
            if download_url:
                return download_url

            return None

        except Exception as e:
            print(f"MakerWorld download URL error: {e}")
            return None

    async def _fetch_download_url(self, instance_id: int, model: Model) -> Optional[str]:
        """Fetch the signed download URL, retrying once on 401."""
        api_url = f"{self.BASE_URL}/api/v1/design-service/instance/{instance_id}/f3mf?type=download"

        response = await self.client.get(api_url)

        # On 401, re-extract token once and retry
        if response.status_code == 401:
            print("MakerWorld: Token expired, re-extracting from local app...")
            self._logged_in = False
            if not self._get_local_token():
                return None
            response = await self.client.get(api_url)

        response.raise_for_status()

        data = response.json()
        download_url = data.get("url")

        if download_url:
            print(f"MakerWorld: Got download URL for {data.get('name', model.name)}")
            return download_url

        return None

    async def _get_instance_id(self, model: Model) -> Optional[int]:
        """Get the best instance ID for a model (preferring X1 Carbon)."""
        if model.id in self._instance_cache:
            return self._instance_cache[model.id]

        try:
            response = await self.client.get(model.url)
            response.raise_for_status()

            match = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                response.text
            )
            if not match:
                return None

            data = json.loads(match.group(1))
            design = data.get("props", {}).get("pageProps", {}).get("design", {})
            instances = design.get("instances", [])

            if not instances:
                return None

            # Prefer X1 Carbon instance, otherwise use first/default
            best_instance = None
            for inst in instances:
                ext = inst.get("extention", {})
                compat = ext.get("modelInfo", {}).get("compatibility", {})
                product = compat.get("devProductName", "")

                if "X1" in product:
                    best_instance = inst
                    break
                if inst.get("isDefault") or not best_instance:
                    best_instance = inst

            if best_instance:
                instance_id = best_instance.get("id")
                self._instance_cache[model.id] = instance_id
                return instance_id

            return None

        except Exception as e:
            print(f"MakerWorld: Error getting instance ID: {e}")
            return None

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
