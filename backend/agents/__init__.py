"""
FATMEE's specialist agents. Each agent is one tool in tools/registry.py.

To add an agent: write agents/<name>.py with its tool function (and a
configure() if it needs the LLM or another shared service), add one
Tool entry in tools/registry.py, and add it below if it has configure().
"""


def configure(llm, images=None) -> None:
    """Give every agent the shared services (called once at startup)."""

    from agents import document, image, research, thinker

    research.configure(llm)
    thinker.configure(llm)
    document.configure(llm)
    image.configure(llm, images)
