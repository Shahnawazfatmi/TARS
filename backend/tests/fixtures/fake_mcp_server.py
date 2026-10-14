"""A tiny MCP server used only by tests: fake web search, no internet."""

from mcp.server.fastmcp import FastMCP

server = FastMCP("fake-search")


@server.tool(name="fake-search")
def fake_search(query: str, max_results: int = 5, time_range: str = "") -> str:
    """Search the web (fake)."""
    lines = ["Detailed Results:", ""]
    for i in range(1, max_results + 1):
        lines += [
            f"Title: {query} result {i}",
            f"URL: https://example.com/{i}",
            f"Content: Fact number {i} about {query}.",
            "",
        ]
    return "\n".join(lines)


@server.tool(name="fake-extract")
def fake_extract(urls: list[str]) -> str:
    """Read web pages (fake)."""
    return "\n".join(f"Page {u}: hello" for u in urls)


if __name__ == "__main__":
    server.run()
