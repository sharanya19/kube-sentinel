"""A pool of Gemini models with automatic fallback.

Gemini's free tier gives each model its own small daily quota, and busy models sometimes return
503 "high demand". The pool tries models in order and moves to the next one when a model is out
of quota or overloaded, so an investigation keeps going instead of failing.

    GEMINI_MODELS=gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite   (in .env)
"""

from __future__ import annotations

import os
from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import GoogleAPIError, GoogleModelNotFoundError, GoogleRateLimitError

DEFAULT_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.7-flash"]

# Errors that mean "this model can't serve us right now, try another one".
# Daily quota (429), retired model (404), overloaded / timed out (503 / 504).
_SKIP_CODES = {429, 404, 500, 503, 504}


def model_names() -> list[str]:
    raw = os.environ.get("GEMINI_MODELS") or os.environ.get("GEMINI_MODEL")
    return [m.strip() for m in raw.split(",") if m.strip()] if raw else list(DEFAULT_MODELS)


def _should_skip(e: Exception) -> bool:
    if isinstance(e, (GoogleRateLimitError, GoogleModelNotFoundError)):
        return True
    code = getattr(e, "code", None) or getattr(getattr(e, "__cause__", None), "code", None)
    return isinstance(e, GoogleAPIError) and code in _SKIP_CODES


class ModelPool:
    def __init__(self, names: list[str] | None = None, on_switch=None):
        self.names = names or model_names()
        self.index = 0
        self.used: list[str] = []
        self.requests = 0
        self.on_switch = on_switch
        self._llms = {n: ChatGoogleGenerativeAI(model=n, temperature=0, max_retries=1, timeout=90)
                      for n in self.names}

    @property
    def current(self) -> str:
        return self.names[self.index]

    async def ainvoke(self, messages: list, tools: list | None = None, schema: Any = None):
        """Call the current model; on quota/overload errors, move to the next model and retry."""
        last_error: Exception | None = None
        while self.index < len(self.names):
            llm = self._llms[self.current]
            runnable = (llm.bind_tools(tools) if tools else
                        llm.with_structured_output(schema) if schema else llm)
            try:
                self.requests += 1
                result = await runnable.ainvoke(messages)
                if self.current not in self.used:
                    self.used.append(self.current)
                return result
            except Exception as e:
                if not _should_skip(e):
                    raise
                last_error = e
                failed = self.current
                self.index += 1
                if self.on_switch:
                    nxt = self.current if self.index < len(self.names) else None
                    self.on_switch(failed, nxt, _short(e))
        raise RuntimeError(f"All Gemini models are unavailable (quota or overload). Tried: "
                           f"{', '.join(self.names)}. Last error: {_short(last_error)}")


def _short(e: Exception | None) -> str:
    s = str(e)
    for marker in ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED", "NOT_FOUND"):
        if marker in s:
            return {"RESOURCE_EXHAUSTED": "daily quota used up", "UNAVAILABLE": "overloaded",
                    "DEADLINE_EXCEEDED": "timed out", "NOT_FOUND": "model not available"}[marker]
    return s[:120]
