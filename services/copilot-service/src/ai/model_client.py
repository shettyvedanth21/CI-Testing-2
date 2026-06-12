import asyncio
from typing import Sequence

from src.config import settings


class AIUnavailableError(Exception):
    pass


class ModelClient:
    def __init__(self):
        self.provider = settings.ai_provider.lower().strip()
        self.model = ""
        self.client = None
        self._available = False

        if self.provider not in {"groq", "gemini", "openai"}:
            return
        if not self.is_provider_configured(self.provider):
            return

        try:
            if self.provider == "groq":
                from groq import Groq

                self.client = Groq(api_key=settings.groq_api_key)
                self.model = settings.groq_model
            elif self.provider == "gemini":
                import google.generativeai as genai

                genai.configure(api_key=settings.gemini_api_key)
                self.client = genai.GenerativeModel("gemini-1.5-flash")
                self.model = "gemini-1.5-flash"
            elif self.provider == "openai":
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
                self.model = "gpt-4o-mini"
            self._available = self.client is not None and bool(self.model)
        except Exception:
            self.client = None
            self.model = ""
            self._available = False

    @staticmethod
    def is_provider_configured(provider: str | None = None) -> bool:
        provider_name = (provider or settings.ai_provider).lower().strip()
        if provider_name == "groq":
            return bool(settings.groq_api_key)
        if provider_name == "gemini":
            return bool(settings.gemini_api_key)
        if provider_name == "openai":
            return bool(settings.openai_api_key)
        return False

    def is_available(self) -> bool:
        return self._available

    async def generate(self, messages: Sequence[dict], max_tokens: int = 1000) -> str:
        if not self.is_available():
            raise AIUnavailableError("AI provider is not configured.")
        try:
            if self.provider == "groq":
                return await asyncio.to_thread(self._generate_groq, messages, max_tokens)
            if self.provider == "gemini":
                return await asyncio.to_thread(self._generate_gemini, messages)
            if self.provider == "openai":
                return await asyncio.to_thread(self._generate_openai, messages, max_tokens)
            raise AIUnavailableError(f"Unsupported provider: {self.provider}")
        except Exception as exc:
            raise AIUnavailableError(str(exc)) from exc

    async def ping(self) -> bool:
        if not self.is_available():
            return False
        try:
            msg = [{"role": "user", "content": "Reply with PONG"}]
            out = await self.generate(msg, max_tokens=16)
            return "PONG" in out.upper()
        except Exception:
            return False

    def _generate_groq(self, messages: Sequence[dict], max_tokens: int) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=list(messages),
            max_tokens=max_tokens,
            temperature=0.1,
        )
        return response.choices[0].message.content or ""

    def _generate_gemini(self, messages: Sequence[dict]) -> str:
        formatted = []
        for m in messages:
            role = m.get("role", "user").upper()
            formatted.append(f"{role}: {m.get('content', '')}")
        response = self.client.generate_content("\n\n".join(formatted))
        return response.text or ""

    def _generate_openai(self, messages: Sequence[dict], max_tokens: int) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=list(messages),
            max_tokens=max_tokens,
            temperature=0.1,
        )
        return response.choices[0].message.content or ""
