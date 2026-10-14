"""
Image models for FATMEE, the way llm.py is for text.

Tries each provider in order (OpenAI first, Gemini as backup by
default) and returns the picture as bytes. To add a provider later:
write one class with a generate(prompt, shape) method and add it to
_PROVIDERS. Nothing else in FATMEE needs to change.
"""

import base64
import logging

import config


logger = logging.getLogger("fatmee.image_gen")


# The picture shapes FATMEE offers, sized for LinkedIn. Each provider
# makes the closest size it supports; the image agent then crops it
# to the exact final size.
SHAPES = {
    "square":    {"size": (1200, 1200), "openai": "1024x1024", "gemini": "1:1"},
    "landscape": {"size": (1200, 627),  "openai": "1536x1024", "gemini": "16:9"},
    "portrait":  {"size": (1080, 1350), "openai": "1024x1536", "gemini": "4:5"},
}


class ImageError(Exception):
    """Raised when no provider can make the picture."""


# ============================================================
# PROVIDERS
# ============================================================

class OpenAIImageProvider:
    """OpenAI Images API (gpt-image models)."""

    name = "openai"

    def __init__(self, api_key: str, model: str, quality: str):
        from openai import OpenAI

        # One retry only: image calls cost more than text calls.
        self.client = OpenAI(api_key=api_key, max_retries=1)
        self.model = model
        self.quality = quality

    def generate(self, prompt: str, shape: str) -> bytes:

        result = self.client.images.generate(
            model=self.model,
            prompt=prompt,
            size=SHAPES[shape]["openai"],
            quality=self.quality,
            n=1,
            timeout=config.IMAGE_TIMEOUT,
        )

        data = result.data[0] if result.data else None

        if not data or not data.b64_json:
            raise ValueError("no image in the response")

        return base64.b64decode(data.b64_json)


class GeminiImageProvider:
    """Gemini image models (generate_content with an IMAGE response)."""

    name = "gemini"

    def __init__(self, api_key: str, model: str):
        from google import genai
        from google.genai import types

        # Fail fast instead of retrying, like the text provider.
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(config.IMAGE_TIMEOUT * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        self.model = model

    def generate(self, prompt: str, shape: str) -> bytes:
        from google.genai import types

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio=SHAPES[shape]["gemini"]),
            ),
        )

        for candidate in response.candidates or []:
            parts = candidate.content.parts if candidate.content else None

            for part in parts or []:
                if part.inline_data and part.inline_data.data:
                    return part.inline_data.data

        raise ValueError("no image in the response")


_PROVIDERS = {
    "openai": lambda: OpenAIImageProvider(config.OPENAI_API_KEY, config.OPENAI_IMAGE_MODEL, config.OPENAI_IMAGE_QUALITY)
    if config.OPENAI_API_KEY else None,
    "gemini": lambda: GeminiImageProvider(config.GEMINI_API_KEY, config.GEMINI_IMAGE_MODEL)
    if config.GEMINI_API_KEY else None,
}


def build_image_providers() -> list:
    """Image providers in the configured order, skipping any without a key."""

    providers = []

    for name in config.IMAGE_PROVIDERS:
        make = _PROVIDERS.get(name)

        if make is None:
            logger.warning("Unknown image provider in FATMEE_IMAGE_PROVIDERS: %s", name)
            continue

        provider = make()

        if provider is None:
            logger.info("Skipping image provider %s: no API key set.", name)
            continue

        providers.append(provider)

    if providers:
        logger.info(
            "Image providers: %s",
            " -> ".join(f"{p.name} ({p.model})" for p in providers),
        )

    else:
        logger.info("No image provider: create_image will say it is not set up.")

    return providers


# ============================================================
# IMAGE GENERATOR
# ============================================================

class ImageGenerator:
    """Single entry point for every image call, with fallback."""

    def __init__(self, providers: list):
        self.providers = providers

    @classmethod
    def from_config(cls) -> "ImageGenerator":
        return cls(build_image_providers())

    def generate(self, prompt: str, shape: str = "square") -> tuple:
        """Make one picture. Returns (image bytes, "provider (model)")."""

        if shape not in SHAPES:
            raise ImageError(f"Unknown shape: {shape}")

        errors = []

        for provider in self.providers:

            try:
                data = provider.generate(prompt, shape)

                if not data:
                    raise ValueError("empty image")

                if errors:
                    logger.info("Image made by fallback provider %s", provider.name)

                return data, f"{provider.name} ({provider.model})"

            except Exception as e:
                logger.warning("%s image call failed: %s", provider.name, e)
                errors.append(f"{provider.name}: {e}")

        if not errors:
            raise ImageError("No image model is set up. Add OPENAI_API_KEY or GEMINI_API_KEY.")

        raise ImageError("All image providers failed. " + " | ".join(errors))
