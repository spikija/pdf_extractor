import streamlit as st
from review_app.components import review_entities
from review_app.display_models import medication_entities


def render(selected, hashes, repo, patient, docs):
    if 'medications' not in selected:
        st.warning('No validated medication artifact available.')
        return
    entry = selected['medications']
    review_entities('Medications by active ingredient', medication_entities(entry), entry,
                    hashes['medications'], repo, patient, 'medications', docs,
                    ['normalized_trade_names', 'normalized_trade_name', 'active_ingredients', 'atc_codes', 'atc_code', 'research_class', 'research_subclass'],
                    ['canonical_drug', 'normalized_trade_names', 'active_ingredients', 'research_class',
                     'research_subclass', 'mention_count', 'exposure_count', 'physician_status'], 'medications')
