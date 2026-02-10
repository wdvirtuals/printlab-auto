"""Abstract base class for search providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class Model:
    """Represents a 3D model from a search provider."""

    id: str
    name: str
    url: str
    thumbnail: Optional[str]
    author: str
    provider: str
    downloads: int = 0
    likes: int = 0
    download_url: Optional[str] = None
    file_count: int = 0  # number of printable files (0 = unknown)

    def display_text(self) -> str:
        """Format model info for display."""
        stats = []
        if self.downloads:
            stats.append(f"📥 {self.downloads:,}")
        if self.likes:
            stats.append(f"❤️ {self.likes:,}")
        stats_str = " | ".join(stats) if stats else ""

        return (
            f"*{self.name}*\n"
            f"by {self.author} ({self.provider})\n"
            f"{stats_str}"
        ).strip()


class SearchProvider(ABC):
    """Abstract base class for model search providers."""

    name: str = "Unknown"

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[Model]:
        """Search for models matching the query.

        Args:
            query: Search terms
            limit: Maximum number of results to return

        Returns:
            List of Model objects
        """
        pass

    @abstractmethod
    async def get_download_url(self, model: Model) -> Optional[str]:
        """Get the direct download URL for a model's .3mf file.

        Args:
            model: The model to get download URL for

        Returns:
            Direct download URL or None if not available
        """
        pass
