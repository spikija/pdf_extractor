from review_app.components import review_entities
from review_app.display_models import diagnosis_entities


def render(selected, hashes, repo, patient, docs):
    import streamlit as st
    for module, title, fields, cols in [
        ('diagnoses', 'Chronic diseases',
         ['canonical_name', 'first_documented_date', 'first_documented_date_precision', 'domain', 'category', 'subcategory', 'status'],
         ['canonical_name', 'first_documented_date', 'status', 'domain', 'category', 'subcategory', 'mention_count', 'document_count', 'review_required', 'physician_status']),
        ('events', 'Clinical events',
         ['event_type', 'event_date', 'event_date_precision', 'side', 'territory', 'location', 'etiology', 'acute_treatment', 'status'],
         ['event_type', 'event_date', 'side', 'territory', 'location', 'etiology', 'acute_treatment', 'mention_count', 'review_required', 'physician_status'])]:
        if module not in selected:
            st.warning(f'{title}: no validated artifact available. Legacy source status is in Overview.')
            continue
        entry = selected[module]
        review_entities(title, diagnosis_entities(entry, module == 'events'), entry, hashes[module], repo,
                        patient, 'diagnoses', docs, fields, cols, module)
