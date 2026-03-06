from openai import AsyncOpenAI

from middleware.config import settings
from middleware.models import QueryType

SYSTEM_PROMPT = """You are a query classifier for a code search system.
Given a user query about code, classify it as exactly one of:
- conceptual: asks about design, architecture, purpose, or "why" questions
- procedural: asks about how to do something, step-by-step processes, or workflows
- factual: asks for specific facts, definitions, locations, or "what/where" questions

Respond with exactly one word: conceptual, procedural, or factual."""


class QueryClassifier:
    def __init__(self) -> None:
        self._openai = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)

    async def classify(self, query: str) -> QueryType:
        try:
            response = await self._openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": query},
                ],
                max_tokens=10,
                temperature=0,
            )
            text = response.choices[0].message.content.strip().lower()
            return QueryType(text)
        except Exception:
            return QueryType.factual
