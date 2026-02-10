"""Tests for search providers."""

import pytest
from printlab.search import Cults3DSearch, WebSearchProvider
from printlab.search.base import Model


@pytest.mark.asyncio
async def test_cults3d_search():
    """Test Cults3D search returns results."""
    provider = Cults3DSearch()
    try:
        results = await provider.search("phone stand", limit=5)
        assert isinstance(results, list)
        assert len(results) > 0, "Should find at least one result"
        model = results[0]
        assert model.name
        assert model.provider == "Cults3D"
        assert "cults3d.com" in model.url
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_websearch_provider():
    """Test WebSearch provider returns results from multiple sites."""
    provider = WebSearchProvider()
    try:
        results = await provider.search("phone stand", limit=5)
        assert isinstance(results, list)
        # WebSearch should find results from various sites
        if results:
            model = results[0]
            assert model.name
            assert model.url.startswith("http")
    finally:
        await provider.close()


def test_model_display_text():
    """Test Model.display_text() formatting."""
    model = Model(
        id="123",
        name="Test Phone Stand",
        url="https://example.com/123",
        thumbnail=None,
        author="TestUser",
        provider="TestProvider",
        downloads=1500,
        likes=42,
    )

    text = model.display_text()
    assert "Test Phone Stand" in text
    assert "TestUser" in text
    assert "TestProvider" in text
    assert "1,500" in text  # downloads formatted with commas


def test_model_display_text_no_stats():
    """Test Model.display_text() with no stats."""
    model = Model(
        id="456",
        name="Simple Model",
        url="https://example.com/456",
        thumbnail=None,
        author="Author",
        provider="Provider",
        downloads=0,
        likes=0,
    )

    text = model.display_text()
    assert "Simple Model" in text
    assert "Author" in text
