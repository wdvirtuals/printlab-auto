"""Claude AI agent for natural language processing."""

from __future__ import annotations

import json
from anthropic import AsyncAnthropic

from ..search.base import Model


class ClaudeAgent:
    """Agent for processing natural language queries and ranking results."""

    def __init__(self, api_key: str):
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = "claude-sonnet-4-20250514"

    async def parse_query(self, user_message: str, previous_query: str | None = None) -> dict:
        """Parse a natural language request into search parameters.

        Args:
            user_message: The user's natural language request
            previous_query: The user's previous search query, if any (for follow-ups)

        Returns:
            Dictionary with 'query' and optional 'filters'
        """
        system_prompt = """You extract search keywords from a user's message for searching Thingiverse (a 3D model database).
Respond with JSON only, no other text.

RULES:
- Extract ONLY the object the user wants. Do NOT add extra words like "3d print", "3d printer", "printable", "model", etc.
- Keep the query short and specific — just the object name.
- Only expand vague requests like "something cool" into a concrete object. If the user already names a specific object, use their exact words.

Format: {"query": "search terms", "filters": {}}

Examples:
- "I need a phone stand" -> {"query": "phone stand", "filters": {}}
- "print me a small planter for succulents" -> {"query": "succulent planter", "filters": {}}
- "something to hold my headphones" -> {"query": "headphone stand", "filters": {}}
- "something cute" -> {"query": "cute animal figurine", "filters": {}}
- "bed scraper" -> {"query": "bed scraper", "filters": {}}
- "a gift for my mom" -> {"query": "decorative vase", "filters": {}}
"""
        if previous_query:
            system_prompt += f"""
The user previously searched for: "{previous_query}"
If their new message is a follow-up (e.g. "more options", "something bigger", "show me different ones", "5 more"), refine or reuse the previous query rather than generating an unrelated one.
For example, if they searched "phone stand" and say "more options", return {{"query": "phone stand", "filters": {{}}}}.
If they say "something wooden", return {{"query": "phone stand wood", "filters": {{"material": "wood"}}}}.
If their message is clearly a new topic, ignore the previous query.
"""

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=256,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        try:
            text = response.content[0].text.strip()
            # Handle potential markdown code blocks
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except (json.JSONDecodeError, IndexError):
            # Fallback: use the original message as query
            return {"query": user_message, "filters": {}}

    async def rank_results(
        self, query: str, models: list[Model], limit: int = 5, user_intent: str | None = None
    ) -> list[Model]:
        """Rank search results based on relevance to the query.

        Args:
            query: The search keywords used
            models: List of models to rank
            limit: Maximum number of results to return
            user_intent: The user's original message (may differ from search keywords)

        Returns:
            Sorted list of the most relevant models
        """
        if len(models) <= 1:
            return models

        # Prepare model summaries for ranking
        model_summaries = []
        for i, m in enumerate(models):
            model_summaries.append(
                f"{i}: {m.name} by {m.author} ({m.provider}) - {m.downloads} downloads"
            )

        intent_line = ""
        if user_intent and user_intent.lower() != query.lower():
            intent_line = f"\nUser's original request: {user_intent}"

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=512,
            system=f"""You rank 3D models for a user searching a 3D printing database.

Ranking rules (strict priority order):
1. DROP IRRELEVANT: Remove anything that clearly doesn't match the search intent. A "Music Box" is never relevant to "bed scraper". Be aggressive about filtering.
2. EXACT MATCHES FIRST: Models whose name closely matches the search keywords rank highest, regardless of download count.
3. CLOSE MATCHES NEXT: Models that are the same type of object but named differently (e.g. "razor blade scraper" for a "bed scraper" search).
4. POPULARITY LAST: Among equally relevant models, prefer higher downloads as a tiebreaker.

Respond with JSON only: {{"ranked_indices": [0, 2, 1, ...]}}
Return up to {limit} indices. Return fewer if fewer are relevant.""",
            messages=[
                {
                    "role": "user",
                    "content": f"Search keywords: {query}{intent_line}\n\nModels:\n"
                    + "\n".join(model_summaries),
                }
            ],
        )

        try:
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text)
            indices = result.get("ranked_indices", list(range(limit)))

            ranked = []
            for idx in indices[:limit]:
                if 0 <= idx < len(models):
                    ranked.append(models[idx])

            print(f"Ranked {len(models)} models → {len(ranked)} relevant")
            return ranked

        except (json.JSONDecodeError, IndexError, KeyError) as e:
            print(f"Ranking JSON parse failed ({e}), using fallback")
            return sorted(models, key=lambda m: m.downloads, reverse=True)[:limit]

    async def generate_response(self, context: str, user_message: str) -> str:
        """Generate a helpful response to the user.

        Args:
            context: Current context (e.g., search results, printer status)
            user_message: The user's message

        Returns:
            Natural language response
        """
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=512,
            system="""You are PrintLab, a friendly 3D printing assistant.
Keep responses concise and helpful. Use casual, conversational tone.
You help users find and print 3D models on their Bambu Lab X1C printer.""",
            messages=[
                {
                    "role": "user",
                    "content": f"Context: {context}\n\nUser: {user_message}",
                }
            ],
        )

        return response.content[0].text
