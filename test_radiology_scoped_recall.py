"""Reviewed morphology aliases, added only after fourteen-case HIGH gate passed."""
import pytest
from test_radiology_v2_pet import findings

@pytest.mark.parametrize('positive,negative,kind',[
 ('Atypische Blutung links frontal, in Zusammenschau mit multiplen Microbleeds und Residuen stattgehabter SAB.','Kein Nachweis einer atypischen Blutung links frontal.','ICH'),
 ('Bekannter alter Parenchymdefekt nach wohl hämorrhagischem Mediateilinfarkt links postzentral.','Kein Nachweis eines Mediateilinfarkts.','ISCHEMIC_LESION'),
 ('Bekannt liquifizierter Substanzdefekt im Sinne eines Mediateilinfarktes links.','Kein Hinweis auf einen Mediateilinfarkt.','ISCHEMIC_LESION'),
 ('Rezenter Mediateilinfarkt rechts mit Beteiligung des posterioren Mediastromgebietes.','Keine Zeichen eines rezenten Mediateilinfarktes.','ISCHEMIC_LESION'),
 ('Diskrete Hämosiderinablagerungen im alten Mediateilinfarkt links.','Kein alter Mediateilinfarkt links.','ISCHEMIC_LESION'),
 ('In der SWI kleinste Hämosiderinablagerungen temporookzipital und infratentoriell.','Keine kleinsten Hämosiderinablagerungen.','CMB'),
 ('FLAIR zeigt teils konfluierende hyperintense Marklagerveränderungen periventrikulär.','Keine hyperintensen Marklagerveränderungen.','WMH'),
 ('Alter ischämischer Defekt postzentral rechts.','Kein ischämischer Defekt.','ISCHEMIC_LESION'),
 ('Kleines rezentes punktförmig diffusionsgestörtes Areal im rechten Gyrus praecentralis.','Kein rezentes punktförmig diffusionsgestörtes Areal.','ISCHEMIC_LESION'),
])
def test_each_reviewed_recall_group_has_exact_evidence_and_negative_control(positive,negative,kind):
    records=[f for f in findings(positive)['findings'] if f['finding_type']==kind]
    assert records and all(f['evidence_text'] in positive for f in records)
    assert not any(f['finding_type']==kind for f in findings(negative)['findings'])

def test_old_infarct_age_not_lost_to_hemorrhagic_etiology_adjective():
    fs=findings('Bekannter alter Parenchymdefekt nach wohl hämorrhagischem Mediateilinfarkt links postzentral.')['findings']
    assert next(f for f in fs if f['finding_type']=='ISCHEMIC_LESION')['temporal_status']=='CHRONIC'
