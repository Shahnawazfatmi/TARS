"""
Image agent: makes a picture sized for LinkedIn and saves it as PNG.

    {"id": "s2", "tool": "create_image", "depends_on": ["s1"],
     "args": {"instruction": "An image for a post about this research",
              "shape": "square"}}

1. When the picture must come from earlier results (or no clear
   description was given), one small text AI call writes the
   picture description. A clear description from the user is used
   as it is, so no call is spent.
2. One image AI call makes the picture (image_gen.py: Gemini by
   default).
3. The code crops it to the exact LinkedIn size and saves it with
   the shared rules in agents/files.py.

No text appears in the picture unless asked for, because image
models often misspell words. The writer picks ONE concrete idea and a
style, and avoids the usual AI-picture cliches (glowing brains,
floating dashboards...), so pictures are specific, not generic.
"""

import io
import json
import logging
import os
import re
from pathlib import Path

import config
from agents import files
from image_gen import SHAPES, ImageError


logger = logging.getLogger("fatmee.image")

_llm = None
_images = None

# Other words people (and the planner) use for each shape.
_SHAPE_WORDS = {
    "square": ("square", "1:1", "post", "feed", "instagram"),
    "landscape": ("landscape", "wide", "horizontal", "banner", "link", "article", "16:9", "1.91:1"),
    "portrait": ("portrait", "tall", "vertical", "4:5", "story"),
}

# Picture styles. The writer picks the one that suits the topic when
# the user names none; the matching line is added to the prompt.
STYLES = {
    "photo": ("Candid documentary photograph taken on a full-frame camera, 35mm lens "
              "at f/2, natural window light, true-to-life colors, subtle film grain. "
              "Real people with natural skin texture, relaxed poses, not looking at "
              "the camera. Real places with everyday clutter and small imperfections. "
              "Screens are photographed at a slight angle with natural reflections, "
              "their content soft and out of focus. Not an illustration, not CGI, "
              "not a stock render, not a UI mockup."),
    "illustration": "Modern editorial illustration: rich textured colors, clear shapes, hand-crafted feel.",
    "3d": "Polished 3D render: soft studio lighting, tactile materials, gentle shadows.",
    "isometric": "Detailed isometric illustration: a miniature scene seen from above at an angle.",
    "graphic": "Bold graphic design: strong flat shapes, high contrast, two or three striking colors.",
}

_STYLE_WORDS = {
    "photo": ("photo", "photograph", "photographic", "realistic", "real", "photo-realistic", "photorealistic"),
    "illustration": ("illustration", "illustrated", "drawing", "flat", "editorial"),
    "3d": ("3d", "3-d", "render", "3d render", "clay"),
    "isometric": ("isometric", "miniature"),
    "graphic": ("graphic", "bold", "poster", "minimal", "minimalist"),
}

# The model draws 1024x1024 / 1536x1024 / 1024x1536 and the code crops
# to the LinkedIn size, so important things must sit where the crop
# keeps them (1536x1024 -> 1200x627 cuts ~20% off the top and bottom).
_FRAMING = {
    "square": "Keep the main subject centred with a little space around it.",
    "landscape": ("Wide banner framing: keep every important subject, including "
                  "people's whole heads, inside the middle 70% of the height, with "
                  "clear space above heads and below hands."),
    "portrait": ("Tall framing: keep every important subject inside the middle 80% "
                 "of the width, with space at the sides."),
}

# If a user's own description already names a look, the default style
# is not added on top of it.
_LOOK_WORDS = (
    "photo", "realistic", "illustration", "illustrated", "cartoon", "drawing",
    "painting", "watercolor", "watercolour", "oil", "sketch", "3d", "render",
    "isometric", "anime", "vector", "pixel", "poster", "graphic", "comic",
)

# Result fields that say nothing about what to draw.
_NOT_FOR_PICTURE = ("sources", "searched_at", "path", "folder", "file_name")


def configure(llm, images) -> None:
    """Give the agent the shared LLM and image generator (called once at startup)."""

    global _llm, _images
    _llm = llm
    _images = images


def pick_shape(shape: str) -> str:
    """'wide' -> 'landscape', '4:5' -> 'portrait', unknown -> 'square'."""

    shape = (shape or "").strip().lower()

    for name, words in _SHAPE_WORDS.items():
        if shape == name or shape in words:
            return name

    return "square"


def pick_style(style: str) -> str:
    """'realistic' -> 'photo', '3D render' -> '3d'. Other words (e.g. 'watercolor') are kept as they are."""

    style = (style or "").strip()
    word = style.lower()

    for name, words in _STYLE_WORDS.items():
        if word == name or word in words:
            return name

    return style


def _names_a_look(text: str) -> bool:
    words = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    return any(word in words for word in _LOOK_WORDS)


def _style_line(style: str) -> str:
    if not style:
        return ""

    return STYLES.get(style, f"Style: {style}.")


def _about(inputs: dict) -> dict:
    """Earlier results without links and file paths, which don't help draw."""

    return {
        step_id: (
            {k: v for k, v in result.items() if k not in _NOT_FOR_PICTURE}
            if isinstance(result, dict) else result
        )
        for step_id, result in (inputs or {}).items()
    }


_CITATION = re.compile(r"\s*\[\d+\]")
_URL = re.compile(r"https?://\S+")


def research_context(inputs: dict, user_request: str = "") -> str:
    """
    A short plain-text brief of what the picture is about, for the image
    model itself: the user's words, plus the topic, summary and key facts
    of earlier results (research, think...). Source numbers and links
    are removed. Capped at FATMEE_IMAGE_CONTEXT_CHARS.
    """

    lines = []

    if user_request.strip():
        lines.append(f"The user asked: {user_request.strip()}")

    for result in (inputs or {}).values():

        if not isinstance(result, dict) or result.get("error"):
            continue

        if result.get("topic"):
            lines.append(f"Topic: {result['topic']}")

        for key in ("summary", "output"):
            if isinstance(result.get(key), str) and result[key].strip():
                lines.append(result[key].strip())

        points = result.get("key_points")

        if isinstance(points, list) and points:
            lines.append("Key facts:")
            lines.extend(f"- {point}" for point in points if str(point).strip())

    text = "\n".join(lines)
    text = _URL.sub("", _CITATION.sub("", text)).strip()

    if len(text) > config.IMAGE_CONTEXT_CHARS:
        text = text[:config.IMAGE_CONTEXT_CHARS].rsplit(" ", 1)[0] + " ..."

    return text


def _default_folder() -> Path:
    if config.IMAGES_DIR:
        return Path(os.path.expanduser(config.IMAGES_DIR))

    return files.desktop_folder("FATMEE Images")


def resolve_folder(folder: str = "") -> Path:
    """Where to save: "" = Desktop/FATMEE Images (see agents/files.py for the rules)."""

    return files.resolve_folder(folder, _default_folder())


def _short_title(text: str) -> str:
    words = re.findall(r"[\w'-]+", text or "")
    return " ".join(words[:8])


def _write_description(instruction: str, description: str, shape: str, style: str, inputs: dict, user_request: str = "") -> dict:
    """
    One small text call: turn the request (and earlier results) into a
    specific picture description.

    It picks ONE concrete idea, turns it into one scene, and avoids the
    usual AI-picture cliches, so pictures don't all look the same.
    """

    text = json.dumps(_about(inputs), ensure_ascii=False, default=str)

    if len(text) > config.IMAGE_INPUT_CHARS:
        text = text[:config.IMAGE_INPUT_CHARS] + " ...(shortened)"

    prompt = f"""
You are FATMEE's image prompt writer. Your job: ONE striking, specific
picture that makes people stop scrolling on LinkedIn.

THE USER'S OWN WORDS:
{user_request or "(not given)"}

REQUEST:
{instruction or "(none)"}

USER'S PICTURE IDEA:
{description or "(none)"}

WHAT THE PICTURE IS ABOUT (results of earlier steps):
{text if inputs else "(none)"}

SHAPE: {shape}
STYLE: {style or "(choose one)"}

Work in three steps:
1. CONCEPT: pick the single most concrete, surprising finding or idea,
   not the general topic. Example: not "AI progress" but "AI 'virtual
   scientists' running drug experiments overnight".
2. SCENE: show that one idea as a specific scene: who or what is in
   it, doing what, where, with real objects from that field (lab
   glassware, shop shelves, delivery vans, hospital monitors...). One
   clear focal point.
3. STYLE: use the given style. If none is given, choose the one that
   suits the topic best: photo, illustration, 3d, isometric or graphic.

WHEN THE TOPIC IS ABOUT AI, THE AI IS THE HERO:
- The AI must be clearly visible, in focus and near the centre, not
  in the background. Someone glancing at the picture must see "AI at
  work here", not just a normal office, shop or hospital.
- Show AI the way it really looks today: a screen or tablet where an AI
  assistant is visibly working (highlighted regions on a medical scan,
  items in a list being sorted or flagged with colors, a waveform), a
  robot or robotic arm, a smart device with a status light, a camera
  system tracking items. Screens show real-looking software, softly
  out of focus, never a flat cartoon icon or a UI mockup.
- Never use the words "dashboard", "title", "header", "label" or
  "interface" in the description: image models then paint misspelled
  words on the screen. Describe screen content only as shapes, colored
  bars, highlighted areas and thumbnails.
- For AI AGENTS, show the agent DOING a task by itself, with people
  supervising: e.g. a screen where an AI agent is working through a
  queue of patient files on its own while a nurse checks one result.

Return JSON only:
{{
  "title": "3-6 word name for the file",
  "concept": "the one idea, in one short sentence",
  "style": "photo, illustration, 3d, isometric or graphic",
  "prompt": "the picture description, 50-100 words: subject, action,
             setting, objects, camera angle, lighting, colors, mood"
}}

NEVER use these cliches: glowing brains, circuit-board lines, holograms
floating in empty air, a robot shaking hands with a person, people in
a meeting room pointing at charts, binary code, plain blue "tech"
backgrounds. (Real screens and devices showing AI at work are fine.)

RULES:
- No text, words, letters, numbers or logos in the picture.
- No real people's faces and no brand logos.
- Choose colors that fit the subject; avoid an all-blue or all-teal look.
- Bright, clear lighting and a subject you recognize at a glance: it
  must read well as a small picture in a fast LinkedIn feed. Avoid
  very dark or murky scenes.
- For the photo style, describe a believable real photograph: a real
  place, real people, real devices that exist today, natural light,
  everyday details. Nothing magical or science-fiction.
- Frame it so the crop to {shape} keeps everything important: people's
  whole heads and hands visible, with space around them.
"""

    result = _llm.json(prompt, component="image prompt")

    return {
        "title": str(result.get("title") or "").strip(),
        "concept": str(result.get("concept") or "").strip(),
        "style": pick_style(str(result.get("style") or "")),
        "prompt": str(result.get("prompt") or "").strip(),
    }


def final_prompt(description: str, shape: str, text_in_image: str = "", style: str = "", context: str = "") -> str:
    """
    What the image model receives:

    1. the picture to make (written by the AI writer, or the user's own),
    2. FATMEE's fixed rules (shape, framing, style, no text), added in
       code so they always apply,
    3. the research context, so the scene shows the real subject.
    """

    prompt = f"Create this picture: {description.strip().rstrip('.')}."
    prompt += f" Composition: {shape} format. {_FRAMING[shape]}"

    if style:
        prompt += f" {_style_line(style)}"

    if text_in_image.strip():
        prompt += (
            f' Include the text "{text_in_image.strip()}" exactly as written, '
            "spelled correctly, large and easy to read."
        )

    else:
        prompt += (
            " Do not include any text, letters, numbers, words, logos or watermarks."
            " Any screen shows only shapes, colored bars, icons and blurred"
            " thumbnails: no titles, headers, labels or captions."
        )

    if context:
        prompt += (
            "\n\nBACKGROUND CONTEXT (what this picture is about, from FATMEE's "
            "research). Use it so the people, place, objects and activity "
            "match the real subject. Do not write any of it as text in the "
            "picture:\n" + context
        )

    return prompt


def fit_to_shape(data: bytes, shape: str):
    """
    Crop and resize to the exact LinkedIn size.

    When the top and bottom must be cut (wide banners), more is cut
    from the bottom: heads are usually near the top, desks and floors
    near the bottom.
    """

    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as picture:
        picture = picture.convert("RGB")

    return ImageOps.fit(
        picture,
        SHAPES[shape]["size"],
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.3),
    )


def create_image(
    description: str = "",
    instruction: str = "",
    shape: str = "square",
    style: str = "",
    text_in_image: str = "",
    file_name: str = "",
    folder: str = "",
    open_after=False,
    inputs: dict | None = None,
    user_request: str = "",
) -> dict:
    """
    Make a picture, crop it to the LinkedIn size, and save it as PNG.

    user_request is the user's own question, passed in by the executor
    (not by the planner), so the writer sees words like "agentic".
    """

    description = (description or "").strip()
    instruction = (instruction or "").strip()
    shape = pick_shape(shape)
    style = pick_style(style)

    if not (description or instruction or inputs):
        return {"error": "The image agent needs a description of the picture."}

    if _images is None or not _images.providers:
        return {"error": "Image making is not set up. Add GEMINI_API_KEY (or OPENAI_API_KEY) to backend/.env."}

    try:
        target = resolve_folder(folder)
    except ValueError as e:
        return {"error": str(e)}

    title = concept = ""

    write = bool(inputs) or not description

    if not style and (write or not _names_a_look(description)):
        # Realistic photos by default (FATMEE_IMAGE_DEFAULT_STYLE),
        # unless the user's own description already names a look.
        style = pick_style(config.IMAGE_DEFAULT_STYLE)

    if write:
        # The picture comes from earlier results, or only a vague
        # request was given: let the AI write a good description.
        try:
            written = _write_description(instruction, description, shape, style, inputs or {}, user_request)
        except Exception as e:
            logger.warning("Image description failed: %s", e)
            return {"error": "Could not write the picture description."}

        if not written["prompt"]:
            return {"error": "Could not write the picture description."}

        description, title, concept = written["prompt"], written["title"], written["concept"]
        style = style or written["style"]

    # Shown in the log so you can see why a picture looks the way it does.
    logger.info("Image idea: %s | style: %s | %s", concept or "(your description)", style or "-", description[:300])

    context = research_context(inputs or {}, user_request) if inputs else ""
    prompt = final_prompt(description, shape, text_in_image, style, context)

    logger.info(
        "Image prompt sent: %d characters, %s",
        len(prompt),
        f"with research context ({len(context)} characters)" if context else "no research context",
    )
    logger.info("Full image prompt: %s", prompt.replace("\n", " | "))

    try:
        data, model = _images.generate(prompt, shape)
    except ImageError as e:
        return {"error": f"Could not make the image. {e}"}

    try:
        picture = fit_to_shape(data, shape)
    except OSError:
        return {"error": "The image model returned something that is not a picture."}

    name = file_name or title or _short_title(instruction or description)
    path = files.unique_path(target, files.safe_file_name(name, ".png", "FATMEE image"))

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        picture.save(str(path), "PNG", optimize=True)
    except OSError as e:
        logger.warning("Could not save %s: %s", path, e)
        return {"error": f"Could not save the image in {target} ({type(e).__name__})."}

    width, height = picture.size
    logger.info("Image saved: %s (%dx%d, %s)", path, width, height, model)

    if files.as_bool(open_after):
        files.open_file(path)

    return {
        "saved": True,
        "path": str(path),
        "file_name": path.name,
        "folder": str(path.parent),
        "shape": shape,
        "style": style,
        "size": f"{width}x{height}",
        "model": model,
        "concept": concept,
        "description": description,
        "used_research_context": bool(context),
    }
