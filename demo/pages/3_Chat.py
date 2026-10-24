import os

import streamlit as st
from common import client, guard, registry

from fli_demo.agent import azure_chat, run_turn

st.title("Chat")
api, reg = client(), registry()
agents = reg.agents()
if not agents:
    st.info("Create an agent first.")
    st.stop()

agent = st.selectbox("Agent", agents, format_func=lambda a: f"{a['name']} ({a['kind']})")
if not agent["libraryIds"]:
    st.warning("This agent has no libraries attached.")

if "chat" not in st.session_state or st.session_state.get("chat_agent") != agent["id"]:
    st.session_state.update(chat=[], chat_agent=agent["id"], history=[])


@st.cache_resource
def model():
    return azure_chat(os.environ["AOAI_ENDPOINT"], os.environ["AOAI_CHAT_DEPLOYMENT"])


names = {lib["id"]: lib["name"] for lib in guard(api.list_libraries) or []}


def show(searches):
    for s in searches:
        charge = f"{s.request_charge} RU" if s.request_charge is not None else "RU not reported"
        label = f"Tool called: {s.tool_name} → {s.library_name} · {s.latency_ms:.0f} ms · {charge}"
        with st.expander(label):
            st.write(f"Query: {s.query}")
            if s.filters:
                st.write("Filters: " + ", ".join(f"{k} {v}" for k, v in s.filters.items()))
            if s.error:
                st.error(s.error)
            else:
                st.write(f"{s.result_count} results ({s.disposition})")
                for note in s.notes:
                    st.caption(note)
                for c in s.citations:
                    st.write("- " + c.label())
        for image in s.images:  # image results (Phase 3B); short-lived read links from the index
            st.image(image["url"], caption=image["caption"])


for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.write(m["content"])
        show(m.get("searches", []))

if question := st.chat_input("Ask a question"):
    st.session_state.chat.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"), st.spinner("Searching…"):
        turn = guard(run_turn, model(), api, agent, st.session_state.history, question, names)
        if turn:
            # keep only user/assistant text between turns; tool traffic is per turn
            st.session_state.history += [
                {"role": "user", "content": question},
                {"role": "assistant", "content": turn.answer},
            ]
            st.write(turn.answer)
            show(turn.searches)
            st.session_state.chat.append({"role": "assistant", "content": turn.answer, "searches": turn.searches})
