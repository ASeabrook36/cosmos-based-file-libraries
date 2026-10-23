import streamlit as st
from common import client, guard

st.title("Admin: shared libraries")
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
                    "generation": d.get("activeGeneration"),
                    "replacing with": (d.get("pendingFile") or {}).get("fileName"),
                    "chunks": d.get("chunkCount"),
                    "detail": d.get("statusDetail"),
                }
                for d in documents
            ],
            use_container_width=True,
        )
        if documents:
            names = {d["id"]: f"{d.get('fileName')} ({d['id'][:8]})" for d in documents}
            chosen = st.selectbox("Document", list(names), format_func=names.get, key=f"pick-{lib['id']}")
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
