"""Tests for Claude agent module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from printlab.agent.claude import ClaudeAgent
from printlab.search.base import Model


@pytest.fixture
def mock_anthropic():
    """Create a mock Anthropic client."""
    with patch("printlab.agent.claude.AsyncAnthropic") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.mark.asyncio
async def test_parse_query_basic(mock_anthropic):
    """Test basic query parsing."""
    # Mock the API response
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"query": "phone stand", "filters": {}}')]
    mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

    agent = ClaudeAgent("fake-api-key")
    result = await agent.parse_query("I need a phone stand")

    assert result["query"] == "phone stand"
    assert "filters" in result


@pytest.mark.asyncio
async def test_parse_query_with_filters(mock_anthropic):
    """Test query parsing extracts filters."""
    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(text='{"query": "planter small", "filters": {"size": "small"}}')
    ]
    mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

    agent = ClaudeAgent("fake-api-key")
    result = await agent.parse_query("small planter for desk")

    assert "planter" in result["query"]
    assert result["filters"].get("size") == "small"


@pytest.mark.asyncio
async def test_rank_results_small_list(mock_anthropic):
    """Test that small lists are returned as-is."""
    agent = ClaudeAgent("fake-api-key")

    models = [
        Model(
            id="1",
            name="Test Model",
            url="https://example.com/1",
            thumbnail=None,
            author="Author1",
            provider="Test",
            downloads=100,
        ),
    ]

    # Should return as-is without API call
    result = await agent.rank_results("test", models, limit=5)
    assert len(result) == 1
    assert result[0].id == "1"

    # Verify no API call was made
    mock_anthropic.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_generate_response(mock_anthropic):
    """Test response generation."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Here are some great phone stand options!")]
    mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

    agent = ClaudeAgent("fake-api-key")
    result = await agent.generate_response("Found 5 models", "find me a phone stand")

    assert "phone stand" in result
