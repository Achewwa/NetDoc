"""LLM client adapters used by the NetDoc agent controller."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


class LLMError(RuntimeError):
    """Raised when an LLM request fails or returns an unusable response."""


class LLMClient(Protocol):
    """Minimal text completion interface used by planner and synthesizer."""

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        """Return text from the configured LLM."""


@dataclass(frozen=True)
class AnthropicConfig:
    """Configuration for an Anthropic Messages compatible endpoint."""

    auth_token: str
    base_url: str
    model: str
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(
        cls,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> "AnthropicConfig":
        """Build config from environment variables and optional CLI overrides."""
        auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
        if not auth_token:
            raise LLMError("Missing ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY.")

        timeout_value = timeout_seconds
        if timeout_value is None:
            timeout_value = float(os.getenv("ANTHROPIC_TIMEOUT_SECONDS", "30"))

        return cls(
            auth_token=auth_token,
            base_url=base_url or os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            model=model or os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
            timeout_seconds=timeout_value,
        )


@dataclass(frozen=True)
class AnthropicMessagesClient:
    """Small standard-library client for Anthropic Messages compatible APIs."""

    config: AnthropicConfig

    @classmethod
    def from_env(
        cls,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> "AnthropicMessagesClient":
        """Create a client from environment variables and optional overrides."""
        config = AnthropicConfig.from_env(
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        return cls(config)

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        """Call the Messages API and return concatenated text content."""
        payload = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        request = urllib.request.Request(
            url=_messages_url(self.config.base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": self.config.auth_token,
                "Authorization": f"Bearer {self.config.auth_token}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"LLM HTTP {exc.code}: {_truncate(body)}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"LLM request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMError("LLM request timed out.") from exc

        try:
            data = json.loads(raw_body)
            return _content_text(data)
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"LLM response could not be parsed: {_truncate(raw_body)}") from exc


def _messages_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/messages"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/messages"
    return f"{normalized}/v1/messages"


def _content_text(response: dict[str, object]) -> str:
    content = response["content"]
    if not isinstance(content, list):
        raise TypeError("content must be a list")

    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)

    text_response = "\n".join(parts).strip()
    if not text_response:
        raise TypeError("response contained no text content")
    return text_response


def _truncate(value: str, limit: int = 500) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."
