import json
import logging
import re
import time

import config
import task_logger


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


def usage_of(response) -> dict | None:
    """
    Token counts from a provider response, only as the provider
    reported them (whole numbers). None when it reported nothing.
    """

    usage = getattr(response, "usage", None) or getattr(response, "usage_metadata", None)

    if usage is None:
        return None

    def pick(*names):
        for name in names:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)

            if isinstance(value, int) and not isinstance(value, bool):
                return value

        return None

    tokens_in = pick("input_tokens", "prompt_tokens", "total_input_tokens", "prompt_token_count")
    tokens_out = pick("output_tokens", "completion_tokens", "total_output_tokens", "candidates_token_count")
    total = pick("total_tokens", "total_token_count")

    if tokens_in is None and tokens_out is None and total is None:
        return None

    if total is None and tokens_in is not None and tokens_out is not None:
        total = tokens_in + tokens_out

    return {"input_tokens": tokens_in, "output_tokens": tokens_out, "total_tokens": total}


# ============================================================
# PROVIDERS
# ============================================================

# A provider's generate() returns the text, or (text, usage) when the
# response reports token counts (see usage_of).

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

        response = self.client.responses.create(**kwargs)

        return response.output_text, usage_of(response)


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

        response = self.client.interactions.create(**kwargs)

        return response.output_text, usage_of(response)


class LocalProvider:
    """Our own model server, via its OpenAI-style Chat Completions API."""

    name = "local"

    def __init__(self, base_url: str, model: str):
        import httpx
        from openai import OpenAI

        # No retries and a short connect wait: if the server is off,
        # fall back to the next provider in seconds, not minutes.
        self.client = OpenAI(
            base_url=base_url,
            api_key=config.LOCAL_LLM_API_KEY,
            max_retries=0,
            timeout=httpx.Timeout(config.LOCAL_LLM_TIMEOUT, connect=5.0),
        )
        self.model = model

    def generate(self, prompt: str, system: str | None, json_mode: bool) -> str:

        messages = [{"role": "user", "content": prompt}]

        if system:
            messages.insert(0, {"role": "system", "content": system})

        kwargs = {
            "model": self.model,
            "messages": messages,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": config.LOCAL_LLM_THINKING}},
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self.client.chat.completions.create(**kwargs)

        return response.choices[0].message.content, usage_of(response)


_PROVIDERS = {
    "local": (LocalProvider, lambda: config.LOCAL_LLM_URL, lambda: config.LOCAL_LLM_MODEL),
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
            logger.info("Skipping %s: not set up (no URL or API key).", name)
            continue

        providers.append(cls(key(), model()))

    if not providers:
        raise RuntimeError(
            "No AI model is set up. Add FATMEE_LOCAL_LLM_URL (or GEMINI_API_KEY "
            "/ OPENAI_API_KEY) to backend/.env (see backend/.env.example)."
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

    Tries each provider in order (the local model first, then Gemini
    by default) and falls back to the next one when a call fails.
    """

    def __init__(self, providers: list):
        self.providers = providers

    @classmethod
    def from_config(cls) -> "LLM":
        return cls(build_providers())

    def _generate(self, prompt: str, system: str | None = None, json_mode: bool = False,
                  component: str | None = None) -> str:

        errors = []

        for provider in self.providers:

            model = getattr(provider, "model", "")
            start = time.perf_counter()

            try:
                output = provider.generate(prompt, system, json_mode)
                text, usage = output if isinstance(output, tuple) else (output, None)

                if not text:
                    raise ValueError("empty response")

                if errors:
                    logger.info("Answered by fallback provider %s", provider.name)

                task_logger.llm_call(provider.name, model, component, time.perf_counter() - start, True, usage)

                return text

            except Exception as e:
                logger.warning("%s call failed: %s", provider.name, e)
                errors.append(f"{provider.name}: {e}")
                task_logger.llm_call(provider.name, model, component, time.perf_counter() - start, False, error=str(e))

        raise LLMError("All LLM providers failed. " + " | ".join(errors))

    def text(self, prompt: str, system_instruction: str | None = None, component: str | None = None) -> str:
        """Return a plain-text answer."""

        return self._generate(prompt, system_instruction, component=component)

    def json(self, prompt: str, component: str = "llm") -> dict:
        """
        Return a JSON object.

        Raises ValueError when the model output cannot be parsed,
        so each component can choose its own fallback.
        """

        text = self._generate(prompt, json_mode=True, component=component)

        logger.debug("RAW %s RESPONSE: %s", component.upper(), text)

        return parse_json(text)
