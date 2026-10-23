import streamlit as st
from common import client, guard, registry

from fli_demo.registry import OFFICIAL, create_personal_agent

st.title("Agents")
api, reg = client(), registry()
shared = guard(api.list_libraries, "shared") or []  # the attach screen lists shared libraries only (spec §5 rule 4)
names = {lib["id"]: lib["name"] for lib in shared}

left, right = st.columns(2)
with left, st.form("official"):
    st.subheader("New official agent")
    name = st.text_input("Name", key="on")
    instructions = st.text_area("Instructions (optional)", key="oi")
    if st.form_submit_button("Create") and name:
        reg.create(OFFICIAL, name, instructions)
        st.rerun()
with right, st.form("personal"):
    st.subheader("New personal agent")
    pname = st.text_input("Name", key="pn")
    if st.form_submit_button("Create with its own personal library") and pname:
        if guard(create_personal_agent, reg, api, pname):
            st.rerun()

for agent in reg.agents():
    with st.expander(f"{agent['name']}  ·  {agent['kind']}"):
        st.caption(f"agent id {agent['id']}")
        if agent["kind"] == OFFICIAL:
            chosen = st.multiselect(
                "Attached shared libraries",
                options=list(names),
                default=[i for i in agent["libraryIds"] if i in names],
                format_func=names.get,
                key=f"a-{agent['id']}",
            )
            if st.button("Save attachments", key=f"s-{agent['id']}"):
                for lib_id in agent["libraryIds"]:
                    if lib_id not in chosen:
                        reg.detach(agent["id"], lib_id)
                for lib_id in chosen:
                    reg.attach(agent["id"], lib_id)
                st.rerun()
            st.markdown("**Force attach via API** (shows the index refusing a personal library)")
            forced = st.text_input("Library id", key=f"f-{agent['id']}")
            if st.button("Force attach and search", key=f"fb-{agent['id']}") and forced:
                try:
                    api.search(forced, agent["id"], "test")
                    st.warning("The index answered: this library is not personal.")
                except Exception as exc:
                    st.error(f"The index refused: {exc}")
        else:
            st.write("Personal library:", agent["libraryIds"][0] if agent["libraryIds"] else "—")
            lib_id = agent["libraryIds"][0]
            files = st.file_uploader("Upload notes", accept_multiple_files=True, key=f"u-{agent['id']}")
            if files and st.button("Upload", key=f"ub-{agent['id']}"):
                for f in files:
                    guard(api.upload, lib_id, f.name, f.getvalue(), f.type)
                st.rerun()
            docs = guard(api.list_documents, lib_id) or []
            st.dataframe([{"file": d.get("fileName"), "status": d["status"]} for d in docs])
        if st.button("Delete agent", key=f"x-{agent['id']}"):
            reg.delete(agent["id"])
            st.rerun()
