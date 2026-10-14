import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import config
import image_gen
import main
from agents import image
from conftest import FakeProvider
from executor import execute_plan, over_limit_steps
from image_gen import SHAPES, ImageError, ImageGenerator
from llm import LLM
from tools import registry


WRITTEN = json.dumps({
    "title": "Robots In Retail",
    "concept": "A robot restocking shelves in a corner shop",
    "style": "Photograph",
    "prompt": "A friendly robot helping a shopkeeper, warm light.",
})


class FakeImageModel:
    """Makes a plain picture at the size the real provider would return."""

    def __init__(self, name="fake", fail=False, data=None):
        self.name = name
        self.model = "fake-image"
        self.fail = fail
        self.data = data
        self.calls = []

    def generate(self, prompt, shape):
        self.calls.append({"prompt": prompt, "shape": shape})

        if self.fail:
            raise RuntimeError("quota reached")

        if self.data is not None:
            return self.data

        width, height = map(int, SHAPES[shape]["openai"].split("x"))
        buffer = io.BytesIO()
        Image.new("RGB", (width, height), (20, 120, 200)).save(buffer, "PNG")
        return buffer.getvalue()


@pytest.fixture
def fake_images(docs_dir, monkeypatch):
    model = FakeImageModel()
    writer = FakeProvider({"image prompt writer": WRITTEN})
    monkeypatch.setattr(image, "_images", ImageGenerator([model]))
    monkeypatch.setattr(image, "_llm", LLM([writer]))
    return model, writer


# ---------------- shapes and prompts ----------------

@pytest.mark.parametrize("asked,shape", [
    ("square", "square"), ("1:1", "square"), ("", "square"), ("weird", "square"),
    ("wide", "landscape"), ("Banner", "landscape"), ("16:9", "landscape"),
    ("tall", "portrait"), ("4:5", "portrait"),
])
def test_pick_shape(asked, shape):
    assert image.pick_shape(asked) == shape


@pytest.mark.parametrize("asked,style", [
    ("photo", "photo"), ("Realistic", "photo"), ("3D render", "3d"), ("isometric", "isometric"),
    ("poster", "graphic"), ("", ""), ("watercolor", "watercolor"),
])
def test_pick_style(asked, style):
    assert image.pick_style(asked) == style


def test_style_is_added_in_code():
    assert "editorial photograph" in image.final_prompt("A shop", "square", style="photo")
    assert "Style: watercolor." in image.final_prompt("A shop", "square", style="watercolor")
    assert "Style" not in image.final_prompt("A shop", "square")


def test_no_text_unless_asked():
    assert "Do not include any text" in image.final_prompt("A cat.", "square")
    with_text = image.final_prompt("A cat", "square", "AI in Retail")
    assert '"AI in Retail" exactly' in with_text and "Do not include" not in with_text


@pytest.mark.parametrize("shape,made,final", [
    ("square", (1024, 1024), (1200, 1200)),
    ("landscape", (1536, 1024), (1200, 627)),
    ("portrait", (1024, 1536), (1080, 1350)),
])
def test_cropped_to_exact_linkedin_size(shape, made, final):
    buffer = io.BytesIO()
    Image.new("RGB", made).save(buffer, "PNG")
    assert image.fit_to_shape(buffer.getvalue(), shape).size == final


# ---------------- the tool ----------------

def test_clear_description_needs_no_text_call(fake_images):
    model, writer = fake_images
    result = image.create_image(description="A robot reading a newspaper, watercolor", shape="wide")

    assert result["saved"] and result["shape"] == "landscape" and result["size"] == "1200x627"
    assert result["file_name"] == "A robot reading a newspaper watercolor.png"
    assert Image.open(result["path"]).size == (1200, 627)
    # no text call was spent, and the no-text rule was added in code
    assert writer.calls == []
    assert "Do not include any text" in model.calls[0]["prompt"]


def test_picture_from_earlier_results(fake_images):
    model, writer = fake_images
    result = image.create_image(
        instruction="An image for a LinkedIn post about this",
        inputs={"s1": {"topic": "AI in retail", "summary": "Robots help shops."}},
    )

    assert result["file_name"] == "Robots In Retail.png"
    assert result["description"].startswith("A friendly robot")
    assert "Robots help shops." in writer.calls[0]["prompt"]
    assert model.calls[0]["prompt"].startswith("A friendly robot")
    # the writer's style choice is used and added in code
    assert result["style"] == "photo" and result["concept"].startswith("A robot restocking")
    assert "editorial photograph" in model.calls[0]["prompt"]


def test_writer_is_told_to_be_specific_and_skip_links(fake_images):
    _, writer = fake_images
    image.create_image(
        instruction="An image about this",
        inputs={"s1": {"summary": "Virtual scientists test drugs.",
                       "sources": [{"n": 1, "title": "T", "url": "https://secret.example"}]}},
    )
    prompt = writer.calls[0]["prompt"]
    assert "single most concrete" in prompt and "NEVER use these cliches" in prompt
    assert "glowing brains" in prompt and "STYLE: photo" in prompt
    assert "Avoid very dark" in prompt.replace("\n  ", " ")
    # links don't help draw, so they are left out
    assert "Virtual scientists test drugs." in prompt and "secret.example" not in prompt


def test_ai_must_be_the_visible_hero(fake_images):
    _, writer = fake_images
    image.create_image(instruction="An image about this", inputs={"s1": {"summary": "x"}},
                       user_request="Research agentic AI in hospitals and make an image")
    prompt = writer.calls[0]["prompt"]
    # the user's own words reach the writer (so "agentic" is not lost)
    assert "Research agentic AI in hospitals" in prompt
    assert "THE AI IS THE HERO" in prompt and "in focus" in prompt
    assert "For AI AGENTS, show the agent DOING a task" in prompt
    # real screens showing AI are allowed; only floating holograms are banned
    assert "Real screens and devices showing AI at work are fine" in prompt
    assert "believable real photograph" in prompt


def test_realistic_photo_is_the_default(fake_images, monkeypatch):
    model, writer = fake_images

    # a plain description gets the realistic photo look
    assert image.create_image(description="A nurse at a monitor")["style"] == "photo"
    assert "Photorealistic" in model.calls[-1]["prompt"]

    # a description that already names a look is left alone
    result = image.create_image(description="A cartoon nurse at a monitor")
    assert result["style"] == "" and "Photorealistic" not in model.calls[-1]["prompt"]

    # with no default, the writer picks the style
    monkeypatch.setattr(config, "IMAGE_DEFAULT_STYLE", "")
    image.create_image(instruction="An image about this", inputs={"s1": {"summary": "x"}})
    assert "STYLE: (choose one)" in writer.calls[-1]["prompt"]


def test_executor_gives_the_users_question_to_the_image(fake_images):
    _, writer = fake_images
    execute_plan({"steps": [
        {"id": "s1", "tool": "get_ram_usage"},
        {"id": "s2", "tool": "create_image", "depends_on": ["s1"], "args": {"instruction": "An image about it"}},
    ]}, question="Show my agentic RAM")
    assert "Show my agentic RAM" in writer.calls[0]["prompt"]


def test_users_style_wins_over_the_writers(fake_images):
    model, writer = fake_images
    result = image.create_image(instruction="An image about this", style="isometric", inputs={"s1": {"summary": "x"}})
    assert result["style"] == "isometric"
    assert "STYLE: isometric" in writer.calls[0]["prompt"]
    assert "isometric illustration" in model.calls[0]["prompt"]


def test_clear_description_with_style_still_needs_no_text_call(fake_images):
    model, writer = fake_images
    result = image.create_image(description="A lighthouse at dawn", style="watercolor")
    assert writer.calls == [] and result["style"] == "watercolor"
    assert "Style: watercolor." in model.calls[0]["prompt"]


def test_never_overwrites_images(fake_images):
    first = image.create_image(description="A cat")
    second = image.create_image(description="A cat")
    assert (first["file_name"], second["file_name"]) == ("A cat.png", "A cat (2).png")


def test_backup_provider_is_used(docs_dir, monkeypatch):
    broken, backup = FakeImageModel("openai", fail=True), FakeImageModel("gemini")
    monkeypatch.setattr(image, "_images", ImageGenerator([broken, backup]))

    result = image.create_image(description="A cat")
    assert result["model"] == "gemini (fake-image)"
    assert len(broken.calls) == 1 and len(backup.calls) == 1


def test_image_errors(docs_dir, monkeypatch):
    assert "needs a description" in image.create_image()["error"]

    monkeypatch.setattr(image, "_images", ImageGenerator([]))
    assert "not set up" in image.create_image(description="A cat")["error"]

    monkeypatch.setattr(image, "_images", ImageGenerator([FakeImageModel(fail=True)]))
    assert "quota reached" in image.create_image(description="A cat")["error"]

    monkeypatch.setattr(image, "_images", ImageGenerator([FakeImageModel(data=b"not a picture")]))
    assert "not a picture" in image.create_image(description="A cat")["error"]

    monkeypatch.setattr(image, "_images", ImageGenerator([FakeImageModel()]))
    assert "only saves files" in image.create_image(description="A cat", folder="C:/Windows")["error"]

    monkeypatch.setattr(image, "_llm", LLM([FakeProvider({"image prompt writer": "not json"})]))
    assert "picture description" in image.create_image(instruction="something nice")["error"]


def test_image_generator_rules(monkeypatch):
    with pytest.raises(ImageError, match="Unknown shape"):
        ImageGenerator([FakeImageModel()]).generate("x", "circle")

    with pytest.raises(ImageError, match="No image model"):
        ImageGenerator([]).generate("x")

    # providers without a key are skipped
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    assert image_gen.build_image_providers() == []


# ---------------- per-question limit ----------------

def _image_step(n, description):
    return {"id": f"s{n}", "tool": "create_image", "args": {"description": description}}


def test_at_most_two_images_per_question(fake_images):
    model, _ = fake_images
    results = execute_plan({"steps": [_image_step(1, "A cat"), _image_step(2, "A dog"), _image_step(3, "A bird")]})

    assert registry.max_per_task("create_image") == config.MAX_IMAGES_PER_TASK == 2
    assert results["create_image"]["saved"] and results["create_image#2"]["saved"]
    assert results["create_image#3"]["limit_reached"] and "at most 2 times" in results["create_image#3"]["error"]
    assert len(model.calls) == 2


def test_recovery_cannot_get_around_the_limit(fake_images):
    earlier = [_image_step(1, "A cat"), _image_step(2, "A dog")]
    assert over_limit_steps([_image_step(3, "A bird")], earlier) == {"s3": 2}

    results = execute_plan({"steps": [_image_step(3, "A bird")]}, earlier_steps=earlier)
    assert results["create_image"]["limit_reached"]
    # tools without a limit are never counted
    assert over_limit_steps([{"id": "s1", "tool": "get_ram_usage"}] * 5) == {}


# ---------------- through the API ----------------

def test_api_make_one_image(fake_images, fake_llm, monkeypatch):
    model, _ = fake_images
    plan = json.dumps({"goal": "image", "steps": [
        {"id": "s1", "step": 1, "action": "image", "tool": "create_image",
         "args": {"description": "A robot reading a newspaper", "shape": "square"}, "depends_on": []},
    ]})
    fake = fake_llm({
        "You are the planning component": plan,
        "Goal Verification": '{"goal_satisfied": true, "reason": "ok", "missing_information": []}',
    })

    body = TestClient(main.app).post("/ask", json={"question": "Make an image of a robot reading a newspaper"}).json()

    assert body["tools_used"] == ["create_image"]
    assert (image.resolve_folder() / "A robot reading a newspaper.png").exists()
    assert len(model.calls) == 1
    # the planner was told when to use create_image
    assert "create_image" in fake.calls[0]["prompt"] and "only when the user asks" in fake.calls[0]["prompt"]
    # the final answer keeps research findings and sources even when files were made
    final = [c for c in fake.calls if c["system"] and "even when a document or image" in c["system"]]
    assert final


def test_answer_keeps_research_when_an_image_is_made(fake_images, fake_llm, monkeypatch):
    def fake_research(topic=""):
        return {"topic": topic, "summary": "Agents found a drug link [1].",
                "sources": [{"n": 1, "title": "Lab news", "url": "https://lab.example"}]}

    monkeypatch.setitem(registry.TOOLS_BY_NAME, "research_topic", registry.Tool("research_topic", fake_research, "x"))
    plan = json.dumps({"goal": "research + image", "steps": [
        {"id": "s1", "step": 1, "action": "research", "tool": "research_topic", "args": {"topic": "agentic AI"}, "depends_on": []},
        {"id": "s2", "step": 2, "action": "image", "tool": "create_image",
         "args": {"instruction": "An image about it", "shape": "wide"}, "depends_on": ["s1"]},
    ]})
    fake = fake_llm({
        "You are the planning component": plan,
        "Goal Verification": '{"goal_satisfied": true, "reason": "ok", "missing_information": []}',
    })

    body = TestClient(main.app).post("/ask", json={"question": "Research agentic AI and make a wide image"}).json()

    assert body["tools_used"] == ["research_topic", "create_image"]
    final = [c for c in fake.calls if c["system"] and "When a file was saved" in c["system"]][0]
    assert "The results include web research" in final["prompt"]
