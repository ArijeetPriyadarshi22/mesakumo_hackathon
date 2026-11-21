"""Display the cached German overview and its original sources."""
import streamlit as st

from ..tender_overview import FIELDS, PHASES, phase_sources


def overview_view(project, rows, phase, overview):
    if not overview:
        return
    st.subheader('Vergabeübersicht')
    st.caption(f'Aktive Phase · {PHASES[phase]}')
    if overview['status'] == 'pending':
        st.info('Die Übersicht wird erstellt oder der Versuch wurde unterbrochen.')
        return
    if overview['status'] != 'ready':
        st.warning(overview['message'])
        return

    docs, passages, _ = phase_sources(project, rows, phase)
    for key, label in FIELDS.items():
        field = overview['fields'][key]
        st.markdown(f'**{label}**')
        if field['status'] == 'fehlt':
            st.caption('In den berücksichtigten Quellen nicht gefunden.')
            continue
        if any(pid not in passages for pid in field['citations']):
            st.warning('Quellenverweis ist für diese Phase nicht verfügbar.')
            continue
        if field['status'] == 'widerspruch':
            st.warning('Widersprüchliche Angaben in den Quellen.')
        st.text(field['text'])
        for pid in field['citations']:
            passage = passages[pid]
            with st.expander(f'{label} · {docs[passage.document_id].name} · {passage.location}'):
                st.caption(f'Passage {pid}')
                st.text(passage.excerpt)
