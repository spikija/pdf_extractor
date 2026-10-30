import streamlit as st
from review_app.artifact_repository import read_json
from pathlib import Path


def render(names, selected, latest):
    for name in names:
        entry = selected.get(name, latest.get(name))
        if not entry:
            st.info(f'{name}: module not run.')
            continue
        st.info(f'{name}: module available — review UI not yet implemented')
        st.caption(f'Version {entry.get("version")} · {entry.get("status")}')
        with st.expander(f'{name}: technical metadata / raw artifact'):
            st.json(entry)
            path = Path(entry.get('artifact', ''))
            if path.is_file() and path.suffix == '.json':
                st.json(read_json(path))
