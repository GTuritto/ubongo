"""The live console channel (v0.6): a browser front that streams a turn as it
runs. `stream_bridge` is the HTTP-free, unit-testable core (per-turn event
streaming over the bus); `app` is the only module importing FastAPI/uvicorn
(the optional `[console]` extra). Additive over `channel.run_turn` — the stream
observes, it never drives orchestration. See ADR-0023.
"""
