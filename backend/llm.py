import json
import logging
import re

import config


logger = logging.getLogger("fatmee.llm")


class LLMError(Exception):
    """Raised when no provider can produce a usable response."""


def parse_json(text: str) -> dict:
    """
    Parse a JSON object from model output.

    Handles plain JSON, ```json fences, bare ``` fences and
    short prose before or after the object.
    """

    if not text:
        raise ValueError("Empty model output.")

    cleaned = text.strip()

    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)

    if fence:
        cleaned = fence.group(1).strip()

    try:
        result = json.loads(cleaned)

    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start == -1 or end <= start:
            raise ValueError("No JSON object found in model output.")

        result = json.loads(cleaned[start:end + 1])

    if not isinstance(result, dict):
        raise ValueError("Model output is not a JSON object.")

    return result


# ============================================================
# PROVIDERS
# ============================================================

class OpenAIProvider:
    """OpenAI Responses API."""

    name = "openai"

    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        # The SDK retries rate limits and server errors itself.
        self.client = OpenAI(api_key=api_key, max_retries=2)
        self.model = model

    def generate(self, prompt: str, system: str | None, json_mode: bool) -> str:

        kwargs = {
            "model": self.model,
            "input": prompt,
            "timeout": config.LLM_TIMEOUT,
        }

        if system:
            kwargs["instructions"] = system

        if json_mode:
            kwargs["text"] = {"format": {"type": "json_object"}}

        return self.client.responses.create(**kwargs).output_text


class GeminiProvider:
    """Gemini Interactions API."""

    name = "gemini"

    def __init__(self, api_key: str, model: str):
        from google import genai

        self.client = genai.Client(api_key=api_key)

        # Fail fast instead of retrying. On a 429 the SDK sleeps for
        # the server's Retry-After time, which can be minutes, and
        # its public retry options cannot go below one retry.
        try:
            self.client.interactions.sdk_configuration.retry_config.max_retries = 0

        except AttributeError:
            logger.debug("Could not disable Gemini SDK retries.")
        self.model = model

    def generate(self, prompt: str, system: str | None, json_mode: bool) -> str:

        kwargs = {
            "model": self.model,
            "input": prompt,
            "timeout": config.LLM_TIMEOUT,
        }

        if system:
            kwargs["system_instruction"] = system

        if json_mode:
            kwargs["response_format"] = {"type": "text", "mime_type": "application/json"}

        return self.client.interactions.create(**kwargs).output_text


_PROVIDERS = {
    "openai": (OpenAIProvider, lambda: config.OPENAI_API_KEY, lambda: config.OPENAI_MODEL),
    "gemini": (GeminiProvider, lambda: config.GEMINI_API_KEY, lambda: config.GEMINI_MODEL),
}


def build_providers() -> list:
    """Providers in the configured order, skipping any without a key."""

    providers = []

    for name in config.LLM_PROVIDERS:
        entry = _PROVIDERS.get(name)

        if entry is None:
            logger.warning("Unknown LLM provider in FATMEE_LLM_PROVIDERS: %s", name)
            continue

        cls, key, model = entry

        if not key():
            logger.info("Skipping %s: no API key set.", name)
            continue

        providers.append(cls(key(), model()))

    if not providers:
        raise RuntimeError(
            "No LLM API key found. Add OPENAI_API_KEY or GEMINI_API_KEY "
            "to backend/.env (see backend/.env.example)."
        )

    logger.info(
        "LLM providers: %s",
        " -> ".join(f"{p.name} ({p.model})" for p in providers),
    )

    return providers


# ============================================================
# LLM
# ============================================================

class LLM:
    """
    Single entry point for every model call in FATMEE.

    Tries each provider in order (OpenAI first, then Gemini by
    default) and falls back to the next one when a call fails.
    """

    def __init__(self, providers: list):
        self.providers = providers

    @classmethod
    def from_config(cls) -> "LLM":
        return cls(build_providers())

    def _generate(self, prompt: str, system: str | None = None, json_mode: bool = False) -> str:

        errors = []

        for provider in self.providers:

            try:
                text = provider.generate(prompt, system, json_mode)

                if not text:
                    raise ValueError("empty response")

                if errors:
                    logger.info("Answered by fallback provider %s", provider.name)

                return text

            except Exception as e:
                logger.warning("%s call failed: %s", provider.name, e)
                errors.append(f"{provider.name}: {e}")

        raise LLMError("All LLM providers failed. " + " | ".join(errors))

    def text(self, prompt: str, system_instruction: str | None = None) -> str:
        """Return a plain-text answer."""

        return self._generate(prompt, system_instruction)

    def json(self, prompt: str, component: str = "llm") -> dict:
        """
        Return a JSON object.

        Raises ValueError when the model output cannot be parsed,
        so each component can choose its own fallback.
        """

        text = self._generate(prompt, json_mode=True)

        logger.debug("RAW %s RESPONSE: %s", component.upper(), text)

        return parse_json(text)
