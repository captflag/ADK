"""Entry point for `adk web agents` and `adk run agents/desk`.

Needs GOOGLE_API_KEY (Gemini) and BATCHWARD_MARG_DB (a Marg database, for example
one written by `batchward demo-marg sim-out/marg.sqlite`), usually set in `.env`.
ADK loads `app` in preference to `root_agent`, so the Numbers Guard always runs.
"""

from batchward.agents.team import build_app

app = build_app()
root_agent = app.root_agent
