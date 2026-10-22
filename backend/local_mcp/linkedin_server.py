"""
FATMEE's LinkedIn MCP server (official LinkedIn API only).

FATMEE starts it from mcp_servers.json and talks to it over MCP
(stdio). Any other MCP client could use it too.

Tools:
    linkedin_status       connected? as whom? until when? (no network)
    linkedin_get_profile  your name, picture, email (read-only)
    linkedin_create_post  publish text, or text + one image, on your
                          profile. FATMEE marks it "needs approval" in
                          mcp_servers.json, so it only runs after you
                          approve the exact text and image. In dry run
                          (the default) it checks everything and
                          publishes nothing.

Logging in happens in FATMEE itself (/linkedin/connect), because it
needs a browser redirect; this server only reads the saved,
encrypted token.
"""

import os
import sys

# Run as a script: make backend/ importable (config, connectors).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from connectors import linkedin  # noqa: E402


server = FastMCP("fatmee-linkedin")


def _safe(action):
    """Return LinkedIn problems as {"error": ...} instead of crashing the tool."""

    try:
        return action()
    except linkedin.LinkedInError as e:
        return {"error": str(e)}
    except Exception as e:  # network problems etc.; never include secrets
        return {"error": f"LinkedIn request failed ({type(e).__name__}). Check the internet connection."}


@server.tool(name="linkedin_status")
def linkedin_status() -> dict:
    """Whether FATMEE is connected to LinkedIn, as whom, until when, and whether dry run is on."""

    return _safe(linkedin.status)


@server.tool(name="linkedin_get_profile")
def linkedin_get_profile() -> dict:
    """The connected LinkedIn member's name, profile picture and email (read-only)."""

    return _safe(linkedin.get_profile)


@server.tool(name="linkedin_create_post")
def linkedin_create_post(text: str, image_path: str = "", alt_text: str = "", visibility: str = "PUBLIC") -> dict:
    """
    Publish a post on the connected member's LinkedIn profile: text, or
    text plus one PNG/JPG/GIF image (a local file path). visibility is
    PUBLIC or CONNECTIONS. Returns the post link.
    """

    return _safe(lambda: linkedin.create_post(text, image_path, alt_text, visibility))


if __name__ == "__main__":
    server.run()
