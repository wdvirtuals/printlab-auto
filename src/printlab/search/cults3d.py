"""Cults3D search provider."""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup
from typing import Optional

from .base import SearchProvider, Model


class Cults3DSearch(SearchProvider):
    """Search provider for Cults3D (cults3d.com)."""

    name = "Cults3D"
    BASE_URL = "https://cults3d.com"

    def __init__(self):
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=30.0,
            follow_redirects=True,
        )

    async def search(self, query: str, limit: int = 10) -> list[Model]:
        """Search Cults3D for models."""
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/en/search",
                params={"q": query},
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            models = []
            seen_hrefs = set()

            # Find all links that contain images (these are the model cards)
            for img in soup.find_all("img", src=True):
                src = img.get("src", "")

                # Only look at Cults3D CDN images
                if "cults3d.com" not in src and "images.cults3d" not in src:
                    continue

                # Find parent link
                parent_link = img.find_parent("a", href=True)
                if not parent_link:
                    continue

                href = parent_link.get("href", "")
                if "/3d-model/" not in href or href in seen_hrefs:
                    continue

                seen_hrefs.add(href)

                # Extract model ID from URL
                model_id = href.split("/")[-1]

                # Get title - try multiple sources
                title = parent_link.get("title", "")
                if not title:
                    # Look for title in nearby elements
                    parent_div = parent_link.find_parent("div")
                    if parent_div:
                        title_elem = parent_div.find(["h2", "h3", "h4", "span"], class_=lambda x: x and "title" in str(x).lower())
                        if title_elem:
                            title = title_elem.get_text(strip=True)

                if not title or len(title) < 2:
                    # Use model ID as fallback, make it readable
                    title = model_id.replace("-", " ").title()

                # Try to find author
                author = "Unknown"
                parent_div = parent_link.find_parent("div")
                if parent_div:
                    author_link = parent_div.find("a", href=lambda x: x and "/en/@" in str(x))
                    if author_link:
                        author = author_link.get_text(strip=True)
                        if not author:
                            # Extract from href
                            author = author_link.get("href", "").split("@")[-1]

                model = Model(
                    id=model_id,
                    name=title[:100],
                    url=f"{self.BASE_URL}{href}" if href.startswith("/") else href,
                    thumbnail=src,
                    author=author if author else "Unknown",
                    provider=self.name,
                    downloads=0,
                    likes=0,
                )
                models.append(model)

                if len(models) >= limit:
                    break

            return models

        except Exception as e:
            print(f"Cults3D search error: {e}")
            return []

    async def get_download_url(self, model: Model) -> Optional[str]:
        """Get download URL for a Cults3D model.

        Note: Cults3D requires login for most downloads.
        Returns the model page URL for manual download.
        """
        return model.url

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
