"""Synthetic diagnostic-procedure extraction and verified commit tests."""
from dataclasses import replace
from datetime import date
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from sqlalchemy.sql.elements import Null
from caa_pdf_extractor.database.models import Examination, ExtractionRun
from caa_pdf_extractor.extraction.examination_inputs import select_examination_inputs
from caa_pdf_extractor.extraction.examination_validators import validate_examinations
from caa_pdf_extractor.extraction.examinations import extract_patient
from caa_pdf_extractor.llm.backend import get_client
from caa_pdf_extractor.llm.examination_prompts import examination_prompt
from caa_pdf_extractor.preparation.models import PatientDocumentBundle, PreparedDocument, PreparedSection


def section(text='ECHOKARDIOGRAPHIE (15.12.2015)\nGute systolische Pumpfunktion.', sid='doc1_sec01', **changes):
    return replace(PreparedSection(sid, '123', 1, None, 4, 4, text, text), **changes)


def bundle(*sections):
    return PatientDocumentBundle('123', [PreparedDocument(1, 'synthetic.pdf', '123/synthetic.pdf',
        'hash', 'unknown', None, list(sections))])


def item(s=None): return select_examination_inputs(bundle(s or section()))[0]


def payload(i=None):
    i = i or item()
    return {'patient_id': i.patient_id, 'examination_id': i.examination_id, 'records': [
        {'type': i.type, 'location': i.location, 'date_examination': i.date_examination,
         'neck_vessel_result': None, 'result': i.text, 'evidence': i.text,
         'source_document_id': i.source_document_id, 'section_id': i.section_id, 'page_number': i.page_number}]}


class ValidationTests(unittest.TestCase):
    def test_focused_selection_excludes_radiology_medication_and_laboratory(self):
        text='Computertomografie: Schädel nativ\nICH\nECHOKARDIOGRAPHIE (15.12.2015)\nHerzbefund.\nAmbulanzbrief (29.12.2015)\nLaborwerte\nGlucose 100'
        inputs=select_examination_inputs(bundle(section(text)))
        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0].text, 'ECHOKARDIOGRAPHIE (15.12.2015)\nHerzbefund.')
        self.assertEqual(inputs[0].date_examination,'2015-12-15')
        self.assertEqual(inputs[0].location,'heart')
        for target in ('laboratory','medications'):
            self.assertEqual(select_examination_inputs(bundle(section(candidate_targets=[target]))),[])

    def test_neurological_status_and_planned_ecg_not_selected(self):
        self.assertEqual(select_examination_inputs(bundle(section('Neurologischer Status\nNIHSS 3\n24-Stunden-EKG empfohlen.'))),[])
        self.assertEqual(select_examination_inputs(bundle(section('Sonographie empfohlen\nTermin nächste Woche.'))),[])

    def test_bilateral_duplex_preserves_separate_side_and_excludes_therapy(self):
        text='Venöse Duplexsonographie der rechten UE:\nThrombosierte Vene rechts.\nVenöse Duplexsonographie der linken UE:\nVenen komprimierbar.\nBeurteilung:\nTVT rechts\nTherapieempfehlung:\nLovenox'
        inputs=select_examination_inputs(bundle(section(text)))
        self.assertEqual(len(inputs),2)
        self.assertTrue(all(i.type=='ultrasound' and i.location is None and i.date_examination is None for i in inputs))
        self.assertNotIn('TVT',inputs[1].text)
        self.assertNotIn('Lovenox',inputs[1].text)

    def test_valid_extraction_and_exact_evidence(self):
        i=item(); data=payload(i)
        result=validate_examinations(json.dumps(data),i)
        self.assertEqual(result['records'][0]['type'],'ultrasound')
        self.assertEqual(result['records'][0]['date_examination'],'2015-12-15')
        self.assertEqual(result['records'][0]['evidence'],i.text)

    def test_unknown_fields_null_and_empty_result_list(self):
        i=item(section('Venöse Duplexsonographie der rechten UE:\nVenen komprimierbar.'))
        r=validate_examinations(json.dumps(payload(i)),i)['records'][0]
        self.assertIsNone(r['location']); self.assertIsNone(r['date_examination']); self.assertIsNone(r['neck_vessel_result'])
        p=payload(i); p['records']=[]
        self.assertEqual(validate_examinations(json.dumps(p),i)['records'],[])

    def test_evidence_is_derived_only_from_a_source_validated_result(self):
        i=item(); p=payload(i); p['records'][0].pop('evidence')
        r=validate_examinations(json.dumps(p),i)['records'][0]
        self.assertEqual(r['evidence'],i.text)
        p['records'][0]['result']='Unsupported paraphrase'
        with self.assertRaises(ValueError): validate_examinations(json.dumps(p),i)

    def test_provenance_and_known_metadata_are_attached_from_input_not_model(self):
        i=item()
        p={'patient_id':i.patient_id,'examination_id':i.examination_id,'records':[{'result':i.text}]}
        r=validate_examinations(json.dumps(p),i)['records'][0]
        self.assertEqual(r['source_document_id'],i.source_document_id)
        self.assertEqual(r['section_id'],i.section_id)
        self.assertEqual(r['type'],'ultrasound')
        self.assertEqual(r['location'],'heart')
        self.assertEqual(r['date_examination'],'2015-12-15')

    def test_paraphrase_and_wrong_provenance_rejected(self):
        i=item()
        for changes in ({'evidence':'Paraphrased finding'},
                        {'source_document_id':2}, {'source_document_id':'1'}, {'page_number':5}, {'section_id':'wrong'}):
            p=payload(i); p['records'][0].update(changes)
            with self.assertRaises(ValueError): validate_examinations(json.dumps(p),i)

    def test_concise_verbatim_result_is_accepted(self):
        i=item(); p=payload(i)
        p['records'][0]['result']='Gute systolische Pumpfunktion.'
        p['records'][0]['evidence']='Gute systolische Pumpfunktion.'
        self.assertEqual(validate_examinations(json.dumps(p),i)['records'][0]['result'],'Gute systolische Pumpfunktion.')

    def test_normal_carotid_ultrasound_category_stays_null_not_unknown(self):
        i=item(section('Carotisduplex (11.12.2015)\nKeine Stenose der ICA rechts oder links.'))
        p=payload(i)
        self.assertEqual(i.location,'neck vessels')
        self.assertIsNone(validate_examinations(json.dumps(p),i)['records'][0]['neck_vessel_result'])
        p['records'][0]['neck_vessel_result']='unknown'
        with self.assertRaises(ValueError): validate_examinations(json.dumps(p),i)

    def test_right_ica_explicit_greater_than_fifty(self):
        i=item(section('Duplex der Halsgefäße (11.12.2015)\nICA rechts: Stenose >50%.'))
        p=payload(i); p['records'][0]['neck_vessel_result']='ica > 50perc right stenosis'
        self.assertEqual(validate_examinations(json.dumps(p),i)['records'][0]['neck_vessel_result'],'ica > 50perc right stenosis')

    def test_left_ica_explicit_numeric_severity(self):
        i=item(section('Carotisduplex\nA. carotis interna links: Stenose 60%.'))
        self.assertEqual(validate_examinations(json.dumps(payload(i)),i)['records'][0]['neck_vessel_result'],'ica > 50perc left stenosis')

    def test_right_mca_transcranial_ultrasound(self):
        i=item(section('Transkranielle Sonographie\nMCA rechts: Stenose >50%.'))
        self.assertEqual(i.location,'brain')
        self.assertEqual(validate_examinations(json.dumps(payload(i)),i)['records'][0]['neck_vessel_result'],'acm right > 50 perc stenosis')

    def test_left_mca_explicit_alias(self):
        i=item(section('Neurosonologie\nA. cerebri media links: Stenose 70%.'))
        self.assertEqual(i.location,'brain')
        self.assertEqual(validate_examinations(json.dumps(payload(i)),i)['records'][0]['neck_vessel_result'],'acm left >50perc stenosis')

    def test_other_explicit_cerebral_vessel_stenosis(self):
        i=item(section('Transkranielle Dopplersonographie\nStenose der A. cerebri posterior rechts.'))
        p=payload(i); p['records'][0]['neck_vessel_result']='other cerebral vessel stenosis'
        self.assertEqual(validate_examinations(json.dumps(p),i)['records'][0]['neck_vessel_result'],'other cerebral vessel stenosis')

    def test_vague_threshold_or_negated_stenosis_does_not_qualify(self):
        for line in ('ICA rechts: hochgradige Stenose.', 'ICA rechts: Stenose 50%.',
                     'ICA rechts: Stenose >=50%.', 'ICA rechts: Stenose <60%.',
                     'Keine Stenose >50% der ICA rechts.', 'ICA rechts: Stenose 50-69%.'):
            i=item(section('Carotisduplex\n'+line)); p=payload(i)
            self.assertIsNone(validate_examinations(json.dumps(p),i)['records'][0]['neck_vessel_result'])
            p['records'][0]['neck_vessel_result']='ica > 50perc right stenosis'
            with self.assertRaises(ValueError): validate_examinations(json.dumps(p),i)

    def test_ambiguous_multiple_stenosis_categories_blocks_validation(self):
        i=item(section('Carotisduplex\nICA rechts Stenose 60%; ICA links Stenose 70%.'))
        with self.assertRaises(ValueError): validate_examinations(json.dumps(payload(i)),i)

    def test_percentage_from_other_observation_and_suspected_stenosis_not_classified(self):
        for line in ('ICA rechts Stenose unquantifiziert, EF 60%.', 'Verdacht auf ICA rechts Stenose 70%.', 'ICA rechts Stenose 70%?'):
            i=item(section('Carotisduplex\n'+line))
            self.assertIsNone(validate_examinations(json.dumps(payload(i)),i)['records'][0]['neck_vessel_result'])

    def test_mixed_neck_and_brain_header_stops_selection(self):
        with self.assertRaises(ValueError):
            select_examination_inputs(bundle(section('Carotisduplex und transkranielle Sonographie\nBefund.')))

    def test_pet_amyloid(self):
        i=item(section('PET-CT Amyloid (2015-12-11)\nAmyloid-Tracer-Anreicherung im Gehirn.'))
        self.assertEqual(i.type,'pet ct amyloid')
        self.assertEqual(i.location,'brain')
        self.assertEqual(validate_examinations(json.dumps(payload(i)),i)['records'][0]['type'],'pet ct amyloid')

    def test_pet_fdg(self):
        i=item(section('FDG-PET/CT (11.12.2015)\nMinderaufnahme frontal.'))
        self.assertEqual(i.type,'pet ct fdg')
        self.assertEqual(validate_examinations(json.dumps(payload(i)),i)['records'][0]['type'],'pet ct fdg')

    def test_spect(self):
        i=item(section('SPECT brain (11.12.2015)\nVerminderte Perfusion frontal.'))
        self.assertEqual((i.type,i.location),('spect','brain'))
        self.assertEqual(validate_examinations(json.dumps(payload(i)),i)['records'][0]['type'],'spect')

    def test_mri_body_excludes_mri_brain(self):
        i=item(section('MRI body (11.12.2015)\nKeine Raumforderung.'))
        self.assertEqual((i.type,i.location),('mri body','body'))
        self.assertEqual(select_examination_inputs(bundle(section('MRI brain (11.12.2015)\nICH.'))),[])

    def test_document_or_comparison_date_not_an_examination_date(self):
        for header in ('Carotisduplex: Vergleich CT 11.12.2015','Carotisduplex: Dokument 11.12.2015'):
            i=item(section(header+'\nKeine Stenose.'))
            self.assertIsNone(i.date_examination)

    def test_pydantic_enum_and_date_failure(self):
        i=item()
        for changes in ({'type':'neurological'}, {'location':'left leg'}, {'date_examination':'2015-12-11'},
                        {'neck_vessel_result':'ica > 50% right stenosis'}):
            p=payload(i); p['records'][0].update(changes)
            with self.assertRaises(ValueError): validate_examinations(json.dumps(p),i)

    def test_live_baseline_neck_enum_spelling(self):
        from caa_pdf_extractor.extraction.examination_models import NECK_RESULTS
        self.assertIn('ica > 50perc right stenosis',NECK_RESULTS)
        i=item(section('Carotisduplex (15.12.2015)\nica > 50perc right stenosis'))
        p=payload(i); p['records'][0]['neck_vessel_result']='ica > 50perc right stenosis'
        self.assertEqual(validate_examinations(json.dumps(p),i)['records'][0]['neck_vessel_result'],'ica > 50perc right stenosis')

    def test_mock_backend_no_granite(self):
        with patch.dict(os.environ, {'LLM_BACKEND':'mock'}): c=get_client()
        i=item()
        self.assertEqual(validate_examinations(c.generate_json(examination_prompt(i)),i)['records'],[])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.runs={}; self.rows=[]; self.section=section()
        self.s=MagicMock()
        def add(row):
            if isinstance(row,ExtractionRun): row.id=len(self.runs)+1; self.runs[row.id]=row
            else: row.id=len(self.rows)+1; self.rows.append(row)
        self.s.add.side_effect=add
        self.s.get.side_effect=lambda cls,key:self.runs[key]
        self.s.execute.return_value.all.return_value=[(42,)]
        self.s.scalars.side_effect=lambda stmt:(list(self.runs.values()) if stmt.column_descriptions[0]['entity'] is ExtractionRun
                                               else [r for r in self.rows if r.deleted is False])
        owner=self
        class Transaction:
            def __enter__(self): self.before=list(owner.rows); self.before_runs=dict(owner.runs); return owner.s
            def __exit__(self,typ,value,tb):
                if typ: owner.rows[:]=self.before; owner.runs.clear(); owner.runs.update(self.before_runs)
                else:
                    for row in owner.rows:
                        for name in ('location','type','neck_vessel_result','date_examination'):
                            if isinstance(getattr(row,name),Null): setattr(row,name,None)
                return False
        self.factory=MagicMock(); self.factory.begin.side_effect=Transaction
        self.client=SimpleNamespace(runtime='fake',model_name='fake',model_version='1',generate_json=MagicMock())

    def extract(self, *, dry_run=True, response=None):
        if dry_run: self.client.generate_json.side_effect=None; self.client.generate_json.return_value=response or json.dumps(payload(item(self.section)))
        else: self.client.generate_json.reset_mock(); self.client.generate_json.side_effect=AssertionError('No inference during commit')
        with patch('caa_pdf_extractor.extraction.examinations.build_patient_bundle',return_value=bundle(self.section)), \
             patch('caa_pdf_extractor.extraction.examinations.get_client',side_effect=AssertionError('No backend factory expected')):
            r=extract_patient('123',dry_run=dry_run,client=self.client,session_factory=self.factory,debug_root=self.temp.name)
        if not dry_run: self.client.generate_json.assert_not_called()
        return r

    def artifact(self): return sorted(Path(self.temp.name,'123','verified').glob('examinations_*.json'))[-1]
    def debug(self):
        from caa_pdf_extractor.llm.examination_prompts import PROMPT_VERSION
        return json.loads(Path(self.temp.name,'123','examinations_'+PROMPT_VERSION+'.json').read_text(encoding='utf-8'))

    def test_dry_run_then_inference_free_commit_and_idempotency(self):
        dry=self.extract(); original=self.debug()['normalized_output']
        self.assertEqual(dry['extracted_records'],1); self.assertEqual(self.rows,[])
        self.assertEqual(json.loads(self.artifact().read_text())['validation_status'],'VERIFIED')
        first=self.extract(dry_run=False); second=self.extract(dry_run=False)
        self.assertEqual((first['new_rows_inserted'],first['duplicates_skipped']),(1,0))
        self.assertEqual((second['new_rows_inserted'],second['duplicates_skipped']),(0,1))
        self.assertEqual(self.debug()['normalized_output'],original)
        self.assertEqual(self.rows[0].case_id,42)
        self.assertEqual(self.rows[0].date_examination,date(2015,12,15))
        self.assertFalse(second['granite_called'])

    def test_missing_artifact_and_stale_source(self):
        self.assertEqual(self.extract(dry_run=False)['status'],'NO_VERIFIED_EXTRACTION')
        self.extract(); self.section=section(self.section.text+'\nChanged source')
        self.assertEqual(self.extract(dry_run=False)['status'],'VERIFIED_EXTRACTION_STALE')
        self.assertEqual(self.rows,[])

    def test_changed_prompt_blocks_reuse(self):
        self.extract()
        with patch('caa_pdf_extractor.extraction.examination_artifacts.PROMPT_VERSION','examinations_v2'):
            self.assertEqual(self.extract(dry_run=False)['status'],'VERIFIED_EXTRACTION_STALE')

    def test_model_output_cannot_rewrite_deterministic_source_result(self):
        for raw in ('not JSON',json.dumps({'patient_id':'123'})):
            self.assertEqual(self.extract(response=raw)['status'],'SUCCESS')
            self.client.generate_json.assert_not_called()
            event=self.runs[len(self.runs)].raw_output['normalized_output']
            self.assertIn(event['records'][0]['result'],self.section.text)
            self.assertEqual(self.rows,[])

    def test_case_not_found(self):
        self.s.execute.return_value.all.return_value=[]
        self.assertEqual(self.extract()['status'],'CASE_NOT_FOUND')
        self.assertEqual(self.rows,[])

    def test_null_dates_override_current_date_default(self):
        self.section=section('Venöse Duplexsonographie der rechten UE:\nVenen komprimierbar.')
        self.extract(); self.extract(dry_run=False)
        self.assertIsNone(self.rows[0].date_examination); self.assertIsNone(self.rows[0].location)
        self.assertIsNone(self.rows[0].neck_vessel_result)

    def test_tampered_artifact_is_not_trusted(self):
        from caa_pdf_extractor.extraction.radiology_artifacts import digest
        self.extract(); path=self.artifact(); data=json.loads(path.read_text())
        data.pop('artifact_sha256'); data['inputs'][0]['normalized_output']['records'][0]['evidence']='Invented'
        data['artifact_sha256']=digest(data); path.write_text(json.dumps(data))
        self.assertEqual(self.extract(dry_run=False)['status'],'VERIFIED_EXTRACTION_INVALID')
        self.assertEqual(self.rows,[])

    def test_model_empty_cannot_discard_a_selected_source_report(self):
        p=payload(); p['records']=[]
        self.extract(response=json.dumps(p))
        r=self.extract(dry_run=False)
        self.assertEqual(r['status'],'SUCCESS'); self.assertEqual(r['new_rows_inserted'],1)

    def test_same_identity_different_result_reuses_existing_without_overwrite(self):
        self.extract(); self.extract(dry_run=False)
        self.rows[0].result='Legacy complete report with a different representation.'
        before=self.rows[0].result
        sha=json.loads(self.artifact().read_text())['artifact_sha256']
        result=self.extract(dry_run=False)
        self.assertEqual((result['new_rows_inserted'],result['duplicates_skipped']),(0,1))
        self.assertEqual(self.rows[0].result,before)
        self.assertEqual(len(self.rows),1)
        self.assertEqual(json.loads(self.artifact().read_text())['artifact_sha256'],sha)
        self.assertEqual(self.debug()['runs'][0]['matched_examination_ids'],[1])

    def test_same_identity_same_result_is_duplicate(self):
        self.extract(); self.extract(dry_run=False)
        result=self.extract(dry_run=False)
        self.assertEqual((result['new_rows_inserted'],result['duplicates_skipped']),(0,1))

    def test_different_date_is_new_event(self):
        self.extract(); self.extract(dry_run=False)
        self.rows[0].date_examination=date(2015,12,14)
        self.assertEqual(self.extract(dry_run=False)['new_rows_inserted'],1)

    def test_different_type_is_new_event(self):
        self.extract(); self.extract(dry_run=False)
        self.rows[0].type='other'
        self.assertEqual(self.extract(dry_run=False)['new_rows_inserted'],1)

    def test_different_location_is_new_event(self):
        self.extract(); self.extract(dry_run=False)
        self.rows[0].location='body'
        self.assertEqual(self.extract(dry_run=False)['new_rows_inserted'],1)

    def test_different_neck_category_is_new_event(self):
        self.extract(); self.extract(dry_run=False)
        self.rows[0].neck_vessel_result='ica > 50perc right stenosis'
        self.assertEqual(self.extract(dry_run=False)['new_rows_inserted'],1)

    def test_null_neck_category_matches_null_duplicate(self):
        self.extract(); self.extract(dry_run=False)
        self.assertIsNone(self.rows[0].neck_vessel_result)
        self.assertEqual(self.extract(dry_run=False)['duplicates_skipped'],1)

    def test_distinct_same_day_procedures_without_row_identity_are_reported(self):
        sections=[section('ECHOKARDIOGRAPHIE (15.12.2015)\nBefund der ersten Untersuchung.',sid='doc1_sec01'),
                  section('ECHOKARDIOGRAPHIE (15.12.2015)\nBefund der zweiten Untersuchung.',sid='doc1_sec02')]
        self.client.generate_json.side_effect=[json.dumps(payload(item(s))) for s in sections]
        with patch('caa_pdf_extractor.extraction.examinations.build_patient_bundle',return_value=bundle(*sections)):
            dry=extract_patient('123',client=self.client,session_factory=self.factory,debug_root=self.temp.name)
            self.assertEqual(dry['status'],'SUCCESS')
            self.client.generate_json.reset_mock(); self.client.generate_json.side_effect=AssertionError('No new model call')
            result=extract_patient('123',dry_run=False,client=self.client,session_factory=self.factory,debug_root=self.temp.name)
        self.client.generate_json.assert_not_called()
        self.assertEqual(result['status'],'DUPLICATE_IDENTITY_AMBIGUOUS')
        self.assertEqual(len(result['unresolved_identity_ambiguities']),1)
        self.assertEqual(self.rows,[])

    def test_colliding_legacy_duplex_rows_resolve_by_explicit_labels_then_audit_ids(self):
        sections=[section('Venöse Duplexsonographie der rechten UE:\nVenen rechts komprimierbar.',sid='doc1_sec01'),
                  section('Venöse Duplexsonographie der linken UE:\nVenen links komprimierbar.',sid='doc1_sec02')]
        self.rows=[Examination(id=1,case_id=42,type='ultrasound',location=None,date_examination=None,
                              neck_vessel_result=None,deleted=False,result=s.text) for s in sections]
        self.rows[1].id=2
        self.client.generate_json.side_effect=[json.dumps(payload(item(s))) for s in sections]
        with patch('caa_pdf_extractor.extraction.examinations.build_patient_bundle',return_value=bundle(*sections)):
            extract_patient('123',client=self.client,session_factory=self.factory,debug_root=self.temp.name)
            self.client.generate_json.reset_mock(); self.client.generate_json.side_effect=AssertionError('No inference')
            first=extract_patient('123',dry_run=False,client=self.client,session_factory=self.factory,debug_root=self.temp.name)
            self.assertEqual((first['new_rows_inserted'],first['duplicates_skipped']),(0,2))
            self.assertEqual(first['identity_collisions'][0]['matched_row_ids'],[1,2])
            # Afterwards provenance is sufficient; body wording need not match.
            for r in self.rows: r.result='Different legacy payload representation'
            second=extract_patient('123',dry_run=False,client=self.client,session_factory=self.factory,debug_root=self.temp.name)
        self.client.generate_json.assert_not_called()
        self.assertEqual((second['new_rows_inserted'],second['duplicates_skipped']),(0,2))
        self.assertEqual(len(self.rows),2)
        self.assertTrue(all(r['reuse_reason']=='audited_procedure_identity' for r in self.debug()['runs']))


if __name__=='__main__': unittest.main()
