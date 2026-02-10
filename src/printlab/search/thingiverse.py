"""Thingiverse search provider.

Uses the same anonymous bearer token that Thingiverse's own website uses.
No API app registration or env vars required.
"""

from __future__ import annotations

import httpx
from typing import Optional

from .base import SearchProvider, Model


class ThingiverseSearch(SearchProvider):
    """Search provider for Thingiverse (thingiverse.com).

    Uses the www.thingiverse.com/api proxy with the anonymous bearer token
    embedded in Thingiverse's public JS bundle. No configuration needed.
    """

    name = "Thingiverse"
    BASE_URL = "https://www.thingiverse.com"
    # Must use the www proxy, not api.thingiverse.com (blocked by Cloudflare)
    API_URL = "https://www.thingiverse.com/api"
    # Anonymous token from Thingiverse's app.bundle.js (public, used by all visitors)
    ANON_TOKEN = "56edfc79ecf25922b98202dd79a291aa"

    def __init__(self):
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self.ANON_TOKEN}",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.thingiverse.com/",
            },
            timeout=30.0,
            follow_redirects=True,
        )

    PER_PAGE = 20  # Thingiverse API page size

    async def search(self, query: str, limit: int = 10) -> list[Model]:
        """Search Thingiverse for models, paginating if needed."""
        models = []
        pages_needed = (limit + self.PER_PAGE - 1) // self.PER_PAGE  # ceil division

        try:
            for page in range(1, pages_needed + 1):
                response = await self.client.get(
                    f"{self.API_URL}/search/{query}",
                    params={
                        "type": "things",
                        "per_page": self.PER_PAGE,
                        "page": page,
                        "sort": "popular",
                    },
                )
                response.raise_for_status()
                data = response.json()

                hits = data.get("hits", [])
                if not hits:
                    break

                for item in hits:
                    if "_source" in item:
                        item = item["_source"]

                    creator = item.get("creator", {})
                    author = creator.get("name", "Unknown") if isinstance(creator, dict) else str(creator)

                    model = Model(
                        id=str(item.get("id", "")),
                        name=item.get("name", "Unknown"),
                        url=f"{self.BASE_URL}/thing:{item.get('id')}",
                        thumbnail=item.get("thumbnail", item.get("preview_image")),
                        author=author,
                        provider=self.name,
                        downloads=item.get("make_count", 0),
                        likes=item.get("like_count", 0),
                    )
                    models.append(model)

                    if len(models) >= limit:
                        return models

                # Stop if this page had fewer results than requested
                if len(hits) < self.PER_PAGE:
                    break

            return models

        except Exception as e:
            print(f"Thingiverse search error: {e}")
            return models  # return whatever we got so far

    async def get_download_url(self, model: Model) -> Optional[str]:
        """Get download URL for a Thingiverse model.

        Uses the thing detail endpoint which includes a zip_data field
        with direct CDN URLs for all files. Prefers .3mf then .stl.
        Also sets model.file_count with the number of printable files.
        """
        try:
            response = await self.client.get(
                f"{self.API_URL}/things/{model.id}",
            )
            response.raise_for_status()
            data = response.json()

            zip_data = data.get("zip_data", {})
            files = zip_data.get("files", [])

            if not files:
                return None

            # Count printable files (STL/3MF/OBJ)
            printable = [
                f for f in files
                if f.get("name", "").lower().endswith((".stl", ".3mf", ".obj"))
            ]
            model.file_count = len(printable) if printable else len(files)

            # Look for .3mf files first, then .stl
            best_file = None
            for ext in [".3mf", ".stl"]:
                for f in files:
                    if f.get("name", "").lower().endswith(ext):
                        best_file = f
                        break
                if best_file:
                    break

            # Fallback to first printable file
            if not best_file:
                for f in files:
                    name = f.get("name", "").lower()
                    if name.endswith((".stl", ".3mf", ".obj")):
                        best_file = f
                        break

            if not best_file:
                best_file = files[0]

            return best_file.get("url")

        except Exception as e:
            print(f"Thingiverse download URL error: {e}")
            return None

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
