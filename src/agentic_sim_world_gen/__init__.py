"""Agentic world generation for Isaac Sim: an MCP server, a Claude Code skill and a CLI."""

__version__ = "0.1.0"
REPO_URL = "https://github.com/atinfinity/agentic-sim-world-gen"
# sent with every request to an outside service (SimReady Central, Poly Haven, ambientCG, the asset hosts)
USER_AGENT = f"agentic-sim-world-gen/{__version__} (+{REPO_URL})"
UA = {"User-Agent": USER_AGENT}
