import streamlit as st
from common import client, guard

from fli_demo.proposals import accept_all, proposals_of, review_request, summary

st.title("Admin: shared libraries")
st.page_link("pages/4_Library_Performance.py", label="Open library performance dashboard")


def documents_label(n: int) -> str:
    return f"{n} document" if n == 1 else f"{n} documents"


api = client()

with st.expander("Create a shared library"):
    with st.form("create"):
        name = st.text_input("Name")
        description = st.text_area("Description (this is what the agent's model reads to choose the tool)")
        if st.form_submit_button("Create") and name:
            if guard(api.create_library, "shared", name, description):
                st.rerun()

libraries = guard(api.list_libraries, "shared") or []
if not libraries:
    st.info("No shared libraries yet.")
for lib in libraries:
    stats = lib.get("stats") or {}
    with st.expander(f"{lib['name']}  ·  {stats.get('documentCount', 0)} documents"):
        new_description = st.text_area("Description", lib.get("description", ""), key=f"d-{lib['id']}")
        if st.button("Save description", key=f"s-{lib['id']}") and guard(
            api.update_description, lib["id"], new_description
        ):
            st.success("Saved")
        proposal = lib.get("descriptionProposal")
        if proposal:
            with st.container(border=True):
                generated = (proposal.get("generatedAt") or "")[:16].replace("T", " ")
                st.markdown(
                    f"**Proposed description** · from {documents_label(proposal.get('basedOnDocumentCount', 0))}"
                    f" · {generated} UTC"
                )
                if proposal["text"] == lib.get("description"):
                    st.caption("✓ This is the description agents see.")
                else:
                    edited = st.text_area("Edit before accepting, if you like", proposal["text"], key=f"dp-{lib['id']}")
                    if st.button("Accept this description", type="primary", key=f"da-{lib['id']}") and guard(
                        api.update_description, lib["id"], edited
                    ):
                        st.rerun()
        if stats.get("documentCount") and st.button("Propose a new description", key=f"dq-{lib['id']}"):
            if guard(api.propose_description, lib["id"]):
                st.info("Asked the index for a proposal. Refresh in a few seconds.")
        auto = st.toggle(
            "Accept proposed tags, period and author automatically",
            value=bool(lib.get("autoAcceptTagProposals")),
            key=f"aa-{lib['id']}",
            help="Proposals fill empty fields and add tags; they never replace a value someone set.",
        )
        if auto != bool(lib.get("autoAcceptTagProposals")) and guard(
            api.update_library, lib["id"], autoAcceptTagProposals=auto
        ):
            st.rerun()
        files = st.file_uploader("Upload files", accept_multiple_files=True, key=f"u-{lib['id']}")
        if files and st.button(f"Upload {len(files)} file(s)", key=f"b-{lib['id']}"):
            for f in files:
                guard(api.upload, lib["id"], f.name, f.getvalue(), f.type)
            st.rerun()
        st.button("Refresh status", key=f"r-{lib['id']}")
        documents = guard(api.list_documents, lib["id"]) or []
        counts: dict[str, int] = {}
        for d in documents:
            counts[d["status"]] = counts.get(d["status"], 0) + 1
        st.caption(" · ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no documents")
        st.dataframe(
            [
                {
                    "file": d.get("fileName"),
                    "status": d["status"],
                    "text available": d.get("activeGeneration") is not None,
                    "images": d.get("imageStatus", "ready" if d.get("imageCount") else "—"),
                    "images pending": d.get("imagePendingCount", 0),
                    "stored images": d.get("sourceImageCount"),
                    "searchable images": d.get("imageCount", 0),
                    "generation": d.get("activeGeneration"),
                    "replacing with": (d.get("pendingFile") or {}).get("fileName"),
                    "chunks": d.get("chunkCount"),
                    "tags": ", ".join(d.get("facets") or []),
                    "proposals": len(proposals_of(d)),
                    "detail": d.get("statusDetail"),
                }
                for d in documents
            ],
            use_container_width=True,
        )
        waiting = [d for d in documents if proposals_of(d)]
        if waiting:
            st.markdown(f"**Proposals waiting** · {documents_label(len(waiting))}")
            st.dataframe(
                [{"file": d.get("fileName"), "proposed": summary(d)} for d in waiting],
                use_container_width=True,
            )
            if st.button("Accept all waiting proposals", key=f"pa-{lib['id']}"):
                for d in waiting:
                    guard(api.resolve_proposals, lib["id"], d["id"], accept_all(d))
                st.rerun()
        if documents:
            names = {d["id"]: f"{d.get('fileName')} ({d['id'][:8]})" for d in documents}
            chosen = st.selectbox("Document", list(names), format_func=names.get, key=f"pick-{lib['id']}")
            current = next(d for d in documents if d["id"] == chosen)
            pending = proposals_of(current)
            if pending:
                with st.form(f"prop-{lib['id']}-{chosen}"):
                    st.markdown("**Proposed by the index**: accept, edit then accept, or dismiss")
                    decisions = {}
                    for p in pending:
                        value_col, action_col = st.columns([3, 2])
                        label = {"tag": "Tag (key=value)", "period": "Period (start..end)", "author": "Author"}
                        value = value_col.text_input(label[p.kind], p.value, key=f"pv-{chosen}-{p.item}")
                        action = action_col.radio(
                            "Action",
                            ["accept", "dismiss", "leave"],
                            horizontal=True,
                            key=f"pc-{chosen}-{p.item}",
                            label_visibility="hidden",
                        )
                        decisions[p.item] = (action, value)
                    if st.form_submit_button("Apply"):
                        try:
                            body = review_request(current, decisions)
                        except ValueError as exc:
                            st.error(str(exc))
                            body = None
                        if body and guard(api.update_document, lib["id"], chosen, **body):
                            st.rerun()
            with st.form(f"meta-{lib['id']}-{chosen}"):
                author = st.text_input("Author", current.get("author") or "")
                start = st.text_input("Period start (YYYY-MM-DD)", current.get("periodStart") or "")
                end = st.text_input("Period end (YYYY-MM-DD)", current.get("periodEnd") or "")
                tags = st.text_input("Tags (key=value, comma-separated)", ", ".join(current.get("facets") or []))
                if st.form_submit_button("Save metadata"):
                    pairs = [t.strip().split("=", 1) for t in tags.split(",") if "=" in t]
                    fields = {
                        "author": author.strip() or None,
                        "periodStart": start.strip() or None,
                        "periodEnd": end.strip() or None,
                        "tags": [{"key": k.strip(), "value": v.strip()} for k, v in pairs],
                    }
                    if guard(api.update_document, lib["id"], chosen, **fields):
                        st.success("Saved. Search filters pick it up once the index has updated the chunks.")
            new_file = st.file_uploader("Replace its content", key=f"rf-{lib['id']}")
            replace_col, delete_col = st.columns(2)
            if new_file and replace_col.button("Replace", key=f"rb-{lib['id']}"):
                # The old version stays searchable until the new one is ready.
                if guard(api.replace_document, lib["id"], chosen, new_file.name, new_file.getvalue(), new_file.type):
                    st.rerun()
            if delete_col.button("Delete document", key=f"db-{lib['id']}"):
                guard(api.delete_document, lib["id"], chosen)
                st.rerun()
        if st.checkbox("I want to delete this library and every document in it", key=f"dlc-{lib['id']}"):
            if st.button("Delete library", type="primary", key=f"dl-{lib['id']}"):
                guard(api.delete_library, lib["id"])
                st.rerun()
