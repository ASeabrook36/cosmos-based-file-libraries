import os
import re

import httpx
import streamlit as st
from common import client, guard, registry

from fli_demo.agent import azure_chat, plain_dollars, run_turn
from fli_demo.telemetry import metrics, save_metrics

st.title("Chat")
context_budget = st.sidebar.number_input(
    "Model context budget (tokens)",
    min_value=16000,
    max_value=1000000,
    value=int(os.environ.get("DEMO_CONTEXT_TOKENS", "64000")),
    step=8000,
    help="Set this within the deployed model's context window. Includes history, tools and an answer reserve. "
    "Full-file reading can increase input-token cost and response time.",
)
image_inputs = st.sidebar.checkbox(
    "Send retrieved images to the model",
    value=True,
    help="Turn off for text-only models. Descriptions and image display still work.",
)
api, reg = client(), registry()
agents = reg.agents()
if not agents:
    st.info("Create an agent first.")
    st.stop()

agent = st.selectbox("Agent", agents, format_func=lambda a: f"{a['name']} ({a['kind']})")
if not agent["libraryIds"]:
    st.warning("This agent has no libraries attached.")

if "chat" not in st.session_state or st.session_state.get("chat_agent") != agent["id"]:
    st.session_state.update(chat=[], chat_agent=agent["id"], history=[], image_bytes={}, evidence_ledger=[])


@st.cache_resource
def model():
    return azure_chat(os.environ["AOAI_ENDPOINT"], os.environ["AOAI_CHAT_DEPLOYMENT"])


names = {lib["id"]: lib["name"] for lib in guard(api.list_libraries) or []}


def show(searches):
    for s in searches:
        charge = f"{s.request_charge} RU" if s.request_charge is not None else "RU not reported"
        label = f"Tool called: {s.tool_name} → {s.library_name} · {s.latency_ms:.0f} ms · {charge}"
        with st.expander(label):
            st.write(f"Operation: {s.operation}")
            if s.query:
                st.write(plain_dollars(f"Query: {s.query}"))
            if s.estimated_tokens is not None:
                st.write(f"Estimated input tokens: {s.estimated_tokens:,} · Complete text delivered: {s.complete}")
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


def show_answer(answer, searches):
    # Resolve only IDs returned by the library; model-authored URLs cannot cause
    # server-side requests. Bytes are held in this chat session so SAS expiration
    # does not break an answer already displayed.
    cache = st.session_state.setdefault("image_bytes", {})
    images = {image["imageId"]: image for trace in searches for image in trace.images if image.get("imageId")}
    used = set()

    def display(key, include_caption=True):
        visual = images[key]
        if key not in cache:
            try:
                with httpx.stream("GET", visual["url"], timeout=30, follow_redirects=False) as response:
                    response.raise_for_status()
                    data = bytearray()
                    for part in response.iter_bytes():
                        data.extend(part)
                        if len(data) > 25_000_000:
                            raise ValueError("image too large")
                    while cache and sum(len(value) for value in cache.values()) + len(data) > 100_000_000:
                        del cache[next(iter(cache))]
                    cache[key] = bytes(data)
            except (httpx.HTTPError, ValueError):
                st.caption("This image link is unavailable or expired. Ask the agent to retrieve it again.")
                return
        caption = visual["caption"]
        if include_caption and visual.get("sourceCaption"):
            prefix = "Nearby caption (inferred): " if visual.get("captionAssociation") == "adjacent_inferred" else ""
            caption = prefix + visual["sourceCaption"] + " — " + caption
        st.image(cache[key], caption=caption)
        used.add(key)

    position = 0
    for match in re.finditer(r"!\[([^\]]*)\]\(fli-image:([^\s)?]+)(\?caption=omit)?\)", answer):
        st.write(plain_dollars(answer[position : match.start()]))
        if match[2] in images:
            display(match[2], include_caption=not match[3])
        else:
            st.caption("Image reference was not returned by this retrieval.")
        position = match.end()
    if answer[position:]:
        st.write(plain_dollars(answer[position:]))
    # Unselected search candidates are diagnostics, not pictures asserted by the answer.
    remaining = [key for key in images if key not in used]
    if remaining:
        with st.expander(f"Other image candidates ({len(remaining)}; not selected in this answer)"):
            st.caption(
                "Retrieval candidates may be unrelated. Only images explicitly chosen in the answer appear above."
            )
            for key in remaining:
                st.write("- " + images[key]["caption"])


def show_performance(data):
    if not data:
        return
    charge = f"{data['retrievalRequestCharge']:.2f}"
    if not data["retrievalChargeComplete"]:
        charge = "≥ " + charge
    columns = [*st.columns(2), *st.columns(2)]
    columns[0].metric("Cosmos RU · retrieval", charge)
    columns[1].metric("Retrieval time", f"{data['retrievalMs'] / 1000:.2f} s")
    columns[2].metric("Total response time", f"{data['totalMs'] / 1000:.2f} s" if data["totalMs"] is not None else "—")
    columns[3].metric("Model tokens", f"{data['totalTokens']:,}" if data["totalTokens"] is not None else "Not reported")
    if data["promptTokens"] is not None:
        st.caption(
            f"Actual input: {data['promptTokens']:,} tokens · output: {data['completionTokens']:,} tokens. "
            "Cumulative across model calls; full-document size estimates are separate."
        )
    if data["rateLimitWaitMs"]:
        st.caption(
            f"Quota retry waits: {data['rateLimitWaitMs'] / 1000:.1f} s "
            f"across {data['rateLimitRetries']} retries, included in total response time."
        )
    if any(call.get("finishReason") == "length" for call in data["modelCalls"]):
        st.warning("A model call reached its output token limit; the answer or tool request may be incomplete.")
    with st.expander("Performance breakdown"):
        st.caption(
            "Retrieval time includes API, embedding, Cosmos and Blob work. RU covers reported retrieval "
            "calls; tool-definition and UI metadata reads are excluded. Failed calls may have unreported RU."
        )
        st.write("Retrieval calls")
        st.dataframe(data["tools"], hide_index=True, use_container_width=True)
        st.write("Model calls")
        st.dataframe(
            [{"call": i, **call} for i, call in enumerate(data["modelCalls"], 1)],
            hide_index=True,
            use_container_width=True,
        )
        st.caption(
            "Provider time includes request/network processing, not just model generation. "
            "Cached input is part of input tokens; reasoning is part of output tokens. "
            "They are reported only when supplied by the model provider."
        )


for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        show_answer(m["content"], m.get("searches", []))
        show_performance(m.get("performance"))
        show(m.get("searches", []))

if question := st.chat_input("Ask a question"):
    st.session_state.chat.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(plain_dollars(question))
    with st.chat_message("assistant"), st.spinner("Reading libraries…"):
        turn = guard(
            run_turn,
            model(),
            api,
            agent,
            st.session_state.history,
            question,
            names,
            context_token_budget=int(context_budget),
            image_tokens=int(os.environ.get("DEMO_IMAGE_TOKENS", "8192")),
            image_inputs=image_inputs,
            evidence_ledger=st.session_state.setdefault("evidence_ledger", []),
        )
        if turn:
            performance = metrics(turn)
            try:
                save_metrics(performance)
            except (OSError, ValueError) as exc:
                st.warning(f"The local metrics file could not be written: {type(exc).__name__}")
            # keep only user/assistant text between turns; tool traffic is per turn
            st.session_state.history += [
                {"role": "user", "content": question},
                {"role": "assistant", "content": turn.answer},
            ]
            show_answer(turn.answer, turn.searches)
            show_performance(performance)
            show(turn.searches)
            st.session_state.chat.append(
                {"role": "assistant", "content": turn.answer, "searches": turn.searches, "performance": performance}
            )
