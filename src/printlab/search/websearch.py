"""Web search provider using DuckDuckGo to find 3D models."""

from __future__ import annotations

import re
import httpx
from bs4 import BeautifulSoup
from typing import Optional
from urllib.parse import quote_plus, urlparse

from .base import SearchProvider, Model


class WebSearchProvider(SearchProvider):
    """Search provider using DuckDuckGo to find 3D models across platforms."""

    name = "WebSearch"
    SEARCH_URL = "https://html.duckduckgo.com/html/"

    # Sites to search for 3D models
    MODEL_SITES = [
        "makerworld.com",
        "printables.com",
        "thingiverse.com",
        "cults3d.com",
    ]

    def __init__(self):
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
            timeout=30.0,
            follow_redirects=True,
        )

    async def search(self, query: str, limit: int = 10) -> list[Model]:
        """Search for 3D models using DuckDuckGo."""
        try:
            # Build site-restricted search query
            sites = " OR ".join(f"site:{s}" for s in self.MODEL_SITES)
            search_query = f"{query} 3d model ({sites})"

            response = await self.client.post(
                self.SEARCH_URL,
                data={"q": search_query, "b": ""},
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            models = []
            seen_urls = set()

            # Find search results
            results = soup.find_all("a", class_="result__a")

            for result in results:
                href = result.get("href", "")
                if not href or href in seen_urls:
                    continue

                # Parse the URL to determine the provider
                parsed = urlparse(href)
                domain = parsed.netloc.lower().replace("www.", "")

                # Skip non-model sites
                if not any(site in domain for site in self.MODEL_SITES):
                    continue

                seen_urls.add(href)

                # Determine source site for display
                if "makerworld" in domain:
                    source = "MakerWorld"
                elif "printables" in domain:
                    source = "Printables"
                elif "thingiverse" in domain:
                    source = "Thingiverse"
                elif "cults3d" in domain:
                    source = "Cults3D"
                else:
                    source = domain

                # Extract title
                title = result.get_text(strip=True)
                if not title:
                    title = "Unknown Model"

                # Clean up title (remove site names, etc.)
                for site in ["Thingiverse", "Printables", "MakerWorld", "Cults3D"]:
                    title = title.replace(f" - {site}", "")
                    title = title.replace(f"| {site}", "")

                # Extract model ID from URL
                model_id = parsed.path.split("/")[-1] or href

                model = Model(
                    id=model_id,
                    name=f"{title[:100]} ({source})",
                    url=href,
                    thumbnail=None,
                    author="Unknown",
                    provider=self.name,
                    downloads=0,
                    likes=0,
                )
                models.append(model)

                if len(models) >= limit:
                    break

            return models

        except Exception as e:
            print(f"WebSearch error: {e}")
            return []

    async def get_download_url(self, model: Model) -> Optional[str]:
        """Get download URL - returns model page for manual download."""
        return model.url

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
