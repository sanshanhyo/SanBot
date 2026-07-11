from __future__ import annotations

from typing import Any

import httpx


class LLMError(Exception):
    def __init__(self, message: str, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class OpenAIChatClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: int = 60,
        max_output_tokens: int = 1000,
        temperature: float = 0.8,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def chat(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "stream": False,
        }
        try:
            response = await self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMError("LLM request timed out", "LLM_TIMEOUT") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError("LLM provider rejected the request", f"LLM_HTTP_{exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise LLMError("LLM provider is unavailable", "LLM_UNAVAILABLE") from exc

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("LLM provider returned an invalid response", "LLM_INVALID_RESPONSE") from exc

        text = _content_text(content).strip()
        if not text:
            raise LLMError("LLM provider returned an empty response", "LLM_EMPTY_RESPONSE")
        return text


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)
