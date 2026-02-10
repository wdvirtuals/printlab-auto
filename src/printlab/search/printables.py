"""Printables search provider (Prusa's model repository)."""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup
from typing import Optional

from .base import SearchProvider, Model


class PrintablesSearch(SearchProvider):
    """Search provider for Printables (printables.com)."""

    name = "Printables"
    BASE_URL = "https://www.printables.com"
    API_URL = "https://api.printables.com/graphql/"

    def __init__(self):
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def search(self, query: str, limit: int = 10) -> list[Model]:
        """Search Printables for models using GraphQL API."""
        graphql_query = """
        query SearchModels($query: String!, $limit: Int!) {
            result: searchPrintsV3(
                search: $query
                limit: $limit
                categoryId: null
                hasMake: false
                publishedDateLimitDays: null
                ordering: "-download_count"
            ) {
                items {
                    id
                    name
                    slug
                    image {
                        filePath
                    }
                    user {
                        handle
                    }
                    downloadCount
                    likesCount
                }
            }
        }
        """

        try:
            response = await self.client.post(
                self.API_URL,
                json={
                    "query": graphql_query,
                    "variables": {"query": query, "limit": limit},
                },
            )
            response.raise_for_status()
            data = response.json()

            models = []
            items = data.get("data", {}).get("result", {}).get("items", [])

            for item in items[:limit]:
                image_path = item.get("image", {}).get("filePath", "")
                thumbnail = f"https://media.printables.com/{image_path}" if image_path else None

                model = Model(
                    id=str(item.get("id", "")),
                    name=item.get("name", "Unknown"),
                    url=f"{self.BASE_URL}/model/{item.get('id')}-{item.get('slug', '')}",
                    thumbnail=thumbnail,
                    author=item.get("user", {}).get("handle", "Unknown"),
                    provider=self.name,
                    downloads=item.get("downloadCount", 0),
                    likes=item.get("likesCount", 0),
                )
                models.append(model)

            return models

        except Exception as e:
            print(f"Printables search error: {e}")
            return []

    async def get_download_url(self, model: Model) -> Optional[str]:
        """Get download URL for a Printables model."""
        # Printables requires authentication for direct downloads
        # Return the model page URL for manual download
        return model.url

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
