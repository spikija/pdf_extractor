"""Safety checks for corpus exports: assertions and actual brain examinations."""
import importlib.util
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('corpus_audit', Path(__file__).parents[1] / 'scripts/audit_parser_caa_mri.py')
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def payload(text):
    return dict(schema_version='2.0', document=dict(patient_id='synthetic', filename='synthetic.pdf',
        relative_path='synthetic/synthetic.pdf', sha256='a' * 64, page_count=1, parser='PyMuPDF',
        parser_version=audit.pymupdf.__version__),
        pages=[dict(page_number=1, text=text, headings=[], tables=[])],
        statistics=dict(total_characters=len(text), pages_without_text=0))


@pytest.mark.parametrize('text,status', [
    ('kein Hinweis auf Amyloidangiopathie', 'CAA_NEGATED'),
    ('nicht zum Muster einer Amyloidangiopathie passend', 'CAA_NEGATED'),
    ('kein eindeutiger Nachweis einer Amyloidangiopathie', 'CAA_NEGATED'),
    ('zur Abklärung einer Amyloidangiopathie', 'CAA_DIFFERENTIAL_OR_WORKUP'),
    ('DD Amyloidangiopathie', 'CAA_DIFFERENTIAL_OR_WORKUP'),
    ('Verdacht auf Amyloidangiopathie', 'CAA_SUSPECTED'),
    ('V.a. zerebrale Amyloidangiopathie', 'CAA_SUSPECTED'),
    ('vereinbar mit Amyloidangiopathie', 'CAA_SUSPECTED'),
    ('im Sinne einer Amyloidangiopathie', 'CAA_SUSPECTED'),
    ('bekannte cerebral amyloid angiopathy', 'CAA_ESTABLISHED'),
    ('Amyloidangiopathie', 'CAA_REVIEW_CANDIDATE'),
    ('Bei der Mutter bekannte Amyloidangiopathie', 'CAA_REVIEW_CANDIDATE'),
    ('CAA kann nicht ausgeschlossen werden', 'CAA_DIFFERENTIAL_OR_WORKUP'),
    ('Kein Hinweis auf Infarkt, bekannte Amyloidangiopathie', 'CAA_ESTABLISHED'),
    ('CAA wurde ausgeschlossen', 'CAA_NEGATED'),
    ('V.a Amyloidangiopathie', 'CAA_SUSPECTED'),
    ('A.e. Amyloidangiopathie', 'CAA_SUSPECTED'),
    ('fragliche Amyloidangiopathie', 'CAA_REVIEW_CANDIDATE'),
])
def test_assertions(text, status):
    assert audit.assertion(text) == status


def test_diagnosis_and_marker_scope():
    assert audit.strongest(audit.caa_text(payload('Diagnosen:\nAmyloidangiopathie\n'))) == 'CAA_ESTABLISHED'
    assert audit.strongest(audit.caa_text(payload('Diagnosen:\nV.a.\nAmyloidangiopathie\n'))) == 'CAA_SUSPECTED'
    assert audit.strongest(audit.caa_text(payload('Befund:\nKortikale superfizielle Siderose.\n'))) == 'CAA_REVIEW_CANDIDATE'


@pytest.mark.parametrize('text,present', [
    ('Kernspintomographie: Schädel (01.01.2026)\nGehirn mit unauffälligem Befund. Keine Raumforderung.', True),
    ('MRT Schädel (01.01.2026):\nGehirn mit multiplen lobären Mikroblutungen und Siderose.', True),
    ('Kernspintomographie: HWS (01.01.2026)\nUnauffälliger Befund des Rückenmarks.', False),
    ('Kernspintomographie: Abdomen (01.01.2026)\nUnauffälliger Befund der Leber.', False),
    ('MRT Schädel empfohlen.\n', False),
    ('MRT Schädel geplant (01.01.2026)\nZur Abklärung einer Raumforderung.', False),
    ('Termin für MRT des Schädels.\n', False),
    ('MRT Schädel soll durchgeführt werden.\n', False),
    ('MRT im niedergelassenen Bereich empfohlen.\n', False),
    ('MRT Schädel wurde nicht durchgeführt.\n', False),
    ('MRT des Schädels vom 01.01.2026 zeigte keine Raumforderung.', True),
    ('MRT Knie zeigte keine Raumforderung.\n', False),
    ('MR Schädel (01.01.2026)\nGehirn mit unauffälligem Befund. Keine Raumforderung.', True),
    ('cMRT (01.01.2026)\nSeitenventrikel unauffällig. Keine Raumforderung.', True),
    ('MRT des Schädels wurde durchgeführt.', True),
    ('Kernspintomographie: Schädel und HWS (01.01.2026)\nGehirn unauffällig ohne Raumforderung.', True),
    ('Im MRT zeigte sich\neine cerebrale Ischämie\nohne weitere Blutung.', True),
    ('Ein MRT des Schädels ist\nim niedergelassenen Bereich\nempfohlen.', False),
])
def test_mri_safety(text, present):
    assert bool(audit.brain_mri(payload(text), audit.load_dictionary())) is present


def test_report_deduplication():
    text = ('Kernspintomographie: Schädel (01.01.2026)\nGehirn unauffällig ohne Raumforderung.\n'
            'MRT des Schädels vom 01.01.2026 zeigte keine Raumforderung.')
    assert len(audit.brain_mri(payload(text), audit.load_dictionary())) == 1


def test_library_version_alone_is_not_code_signature(tmp_path):
    index = audit.ParsedIndex()
    source = payload('text')
    row = dict(patient_id='synthetic', filename='synthetic.pdf', relative_path='synthetic/synthetic.pdf', source_hash='a' * 64)
    index.add(source, keep=True)
    assert index.classify(row)[0] == 'OLD_VERSION_PARSED'
    source['document']['parser_signature'] = audit.PARSER_KEY
    index.add(source, keep=True)
    assert index.classify(row)[0] == 'CURRENT_PARSED'
    row['source_hash'] = 'b' * 64
    assert index.classify(row)[0] == 'SOURCE_CHANGED_AFTER_PARSE'


def test_corruption_and_missing():
    index = audit.ParsedIndex()
    row = dict(patient_id='synthetic', filename='synthetic.pdf', relative_path='synthetic/synthetic.pdf', source_hash='a' * 64)
    assert index.classify(row)[0] == 'NEVER_PARSED'
    index.indicated.add(('a' * 64, 'synthetic.pdf'))
    assert index.classify(row)[0] == 'PARSED_ARTIFACT_MISSING'
    broken = payload('text')
    broken['document']['page_count'] = 2
    index.add(broken, keep=True)
    assert index.classify(row)[0] == 'PARSED_ARTIFACT_CORRUPT'
