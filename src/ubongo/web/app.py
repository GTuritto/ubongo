"""Ubongo web UI — a local, self-hosted Streamlit chat page.

Run it (binds the LAN so a tablet on the same network can reach it):

    ./start-ubongo-web.sh
    # or:
    uv run --extra web streamlit run src/ubongo/web/app.py \
        --server.address 0.0.0.0 --server.port 8501

SECURITY: no auth, no TLS — by design, per the single-user home-LAN use case.
Anyone who can reach the page can drive the agent (the Execution Agent's shell
access is still gated by governance + the sandbox, but there is no login). Keep it
on a trusted home network; do not port-forward or expose it to the internet.

This is an additive channel: it calls the same `master.handle` seam as the REPL
and one-shot via `web.turn.run_turn`. It does not start the GP loop or vault
watcher (those are REPL-managed) — it is the turn path only.
"""

from __future__ import annotations

import streamlit as st

from ubongo.config import ConfigError
from ubongo.repl import DEFAULT_PERSONA, VALID_PERSONAS
from ubongo.web import turn

ASSISTANT = "assistant"
USER = "user"


def _init_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []  # list[{"role", "content"}]
    if "pending_approval" not in st.session_state:
        st.session_state.pending_approval = None  # {"message","persona","auto_mode","approval"}


def _render_history() -> None:
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])


def _render_approval_gate() -> None:
    """A turn that governance held for approval. Mirrors the REPL's y/n: Approve
    re-issues with approved=True; Deny records the decline. Persists the choice to
    governance_decisions.approval_response, like the REPL."""
    from ubongo.memory import store

    pa = st.session_state.pending_approval
    approval = pa["approval"]
    with st.chat_message(ASSISTANT):
        st.warning("This turn needs your approval before it runs.")
        st.markdown(f"**{approval['summary']}**")
        with st.expander("Why this needs approval"):
            st.write(approval.get("why", "(no detail)"))
        approve_col, deny_col = st.columns(2)
        if approve_col.button("Approve", type="primary", use_container_width=True):
            store.update_governance_decision(approval["decision_id"], "y")
            resp = turn.run_turn(
                pa["message"], pa["persona"], auto_mode=pa["auto_mode"], approved=True
            )
            st.session_state.messages.append({"role": ASSISTANT, "content": resp.text})
            st.session_state.pending_approval = None
            st.rerun()
        if deny_col.button("Deny", use_container_width=True):
            store.update_governance_decision(approval["decision_id"], "n")
            st.session_state.messages.append(
                {"role": ASSISTANT, "content": "_Aborted; nothing was done._"}
            )
            st.session_state.pending_approval = None
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="Ubongo", page_icon="🧠", layout="centered")

    try:
        turn.bootstrap()
    except ConfigError as exc:
        st.error(f"Config error: {exc}")
        st.info("Set OPENROUTER_API_KEY in .env and reload.")
        st.stop()

    _init_state()

    with st.sidebar:
        st.title("🧠 Ubongo")
        st.caption("Local self-hosted chat. Home LAN only — no auth.")
        auto_mode = st.toggle("Auto-route persona", value=True)
        persona = st.selectbox(
            "Persona",
            VALID_PERSONAS,
            index=VALID_PERSONAS.index(DEFAULT_PERSONA),
            disabled=auto_mode,
            help="Used as the starting persona; ignored for routing when auto is on.",
        )
        if st.button("Clear conversation", use_container_width=True):
            st.session_state.messages = []
            st.session_state.pending_approval = None
            st.rerun()

    _render_history()

    # A pending approval blocks new input until the user decides.
    if st.session_state.pending_approval is not None:
        _render_approval_gate()
        return

    prompt = st.chat_input("Message Ubongo…")
    if not prompt:
        return

    st.session_state.messages.append({"role": USER, "content": prompt})
    with st.chat_message(USER):
        st.markdown(prompt)

    with st.chat_message(ASSISTANT):
        with st.spinner("Thinking…"):
            resp = turn.run_turn(prompt, persona, auto_mode=auto_mode)

        if resp.approval is not None:
            # Stash the gated turn; rerun to render Approve/Deny buttons.
            st.session_state.messages.append({"role": ASSISTANT, "content": resp.text})
            st.session_state.pending_approval = {
                "message": prompt,
                "persona": persona,
                "auto_mode": auto_mode,
                "approval": resp.approval,
            }
            st.rerun()
        else:
            # Normal turn, reject, ask-clarification, or repair-exhausted: resp.text
            # already carries the user-facing message in every case.
            st.markdown(resp.text)
            st.session_state.messages.append({"role": ASSISTANT, "content": resp.text})


main()
