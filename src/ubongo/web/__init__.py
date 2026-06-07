"""Web channel for Ubongo — a local, self-hosted Streamlit chat UI.

Additive, post-v0.1. A thin webpage that drives the SAME orchestration seam as
the REPL and one-shot (`master.handle` -> `queue.flush_delivered`); no bypass.
The turn logic lives in `turn.py` (no Streamlit import, unit-tested); the
Streamlit UI lives in `app.py`. Streamlit is an optional dependency
(`pip install -e ".[web]"` / `uv sync --extra web`); the core stays lean.
"""
