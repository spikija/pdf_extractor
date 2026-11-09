"""Synthetic multi-type identity tests; direct patient regressions stay private."""
from caa_pdf_extractor import identity_resolution as r
from caa_pdf_extractor import person_identity as base


def svnr(tail='020160',start=100):
    for serial in range(start,1000):
        nine=str(serial)+tail
        check=sum(int(d)*w for d,w in zip(nine,(3,7,9,5,8,4,2,1,6)))%11
        if check<10:return nine[:3]+str(check)+nine[3:]
    raise AssertionError('Synthetic fixture unavailable')


def profile(surname='Muster',given='Anna',birth='1960-01-02',sex='',nationality=''):
    p=r.new_profile();p['demographics'].add((surname,given,birth,birth));p['dobs'].add(birth)
    if sex:p['sex'].add(sex)
    if nationality:p['countries'].add(nationality)
    return p


def add_id(p,value,label='SVNR',source='doc1',country=''):
    p['identifiers'].append(dict(raw=value,value=value,label=label,country=country,source=source))


def test_austrian_literal_and_punctuation_variants():
    value=svnr()
    for literal in [value,value[:4]+' '+value[4:],value[:4]+'-\n'+value[4:],'.'.join(value)]:
        evidence=list(r.identifier_evidence('SVNR: '+literal,'doc'))
        assert len(evidence)==1 and evidence[0]['value']==value
        assert base.valid_svnr(evidence[0]['value'])


def test_foreign_alphanumeric_is_never_stripped_into_svnr():
    p=profile(nationality='DE');raw='Z123456789'
    p['identifiers'].extend(r.identifier_evidence('SVNR: '+raw,'doc'))
    result=r.initial(p)
    assert result['identity_type']==r.FOREIGN and result['foreign']=={('DE',raw)}
    assert not result['at'] and p['identifiers'][0]['raw']==raw


def test_foreign_eleven_digits_not_forced_through_at_validator():
    p=profile(nationality='DE');raw='12345678901'
    add_id(p,raw)
    result=r.initial(p)
    assert result['identity_type']==r.FOREIGN and not result['at']


def test_specific_person_id_has_priority_over_unspecified_policy():
    p=profile(nationality='DE');add_id(p,'Z123456789');add_id(p,'123456789',source='doc2')
    result=r.initial(p)
    assert result['identity_type']==r.FOREIGN and result['foreign']=={('DE','Z123456789')}
    assert len(p['identifiers'])==2


def test_address_country_alone_does_not_make_an_unknown_number_stable():
    p=profile();p['address_countries'].add('DE');add_id(p,'12345678901')
    assert r.initial(p)['identity_type']==r.DEMO
    add_id(p,'12345678901',source='independent_document')
    assert r.initial(p)['identity_type']==r.FOREIGN


def test_two_different_full_foreign_ids_require_review():
    p=profile(nationality='DE');add_id(p,'Z123456789');add_id(p,'Y123456789',source='doc2')
    result=r.initial(p)
    assert result['identity_type']==r.UNRESOLVED and result['status']=='FOREIGN_ID_CONFLICT'


def test_foreign_citizen_without_at_id_can_use_demographics():
    p=profile(nationality='NL')
    result=r.initial(p)
    assert result['identity_type']==r.DEMO and result['status']==r.DEMO_HIGH


def test_foreign_citizenship_does_not_invalidate_a_real_at_id():
    p=profile(nationality='NL');value=svnr();add_id(p,value)
    result=r.initial(p)
    assert result['identity_type']==r.AT and result['token']==(r.AT,value)


def test_nl_specific_bsn_label_is_foreign():
    p=profile(nationality='NL')
    p['identifiers'].extend(r.identifier_evidence('BSN: 123456789','doc'))
    assert r.initial(p)['identity_type']==r.FOREIGN


def test_full_names_transliteration_and_dicom_order():
    assert r.normalized_name('Müller-Lang')==r.normalized_name('MUELLER LANG')
    parsed=r.demographics('Patient: MUSTER^ANNA^MARIA, geboren am 02.01.1960')
    assert parsed['surname']=='MUSTER' and parsed['given']=='ANNA MARIA'
    assert parsed['dob']=='1960-01-02'


def test_secondary_demographic_raw_values_are_preserved():
    value=r.context('Geschlecht: männlich\nNationalität: Österreich')
    assert value['sex']=={'M'} and value['countries']=={'AT'}
    assert value['sex_raw']=={'männlich'} and value['country_raw']=={'Österreich'}


def test_official_foreign_and_at_identity_can_share_a_unique_person():
    austrian=profile();value=svnr();add_id(austrian,value)
    foreign=profile();add_id(foreign,'Z123456789',country='DE',label='KVNR')
    results=r.assign({'at':austrian,'foreign':foreign,'demographic':profile()})
    assert results['foreign']['identity_type']==r.FOREIGN
    assert results['at']['token']==results['foreign']['token']==results['demographic']['token']==(r.AT,value)


def test_full_middle_names_are_not_discarded_for_demographic_merging():
    results=r.assign({'one':profile(given='Anna'),'two':profile(given='Anna Maria')})
    assert results['one']['token']!=results['two']['token']


def test_exact_demographic_encounters_share_person_identity():
    results=r.assign({'one':profile('Müller','Anna'),'two':profile('Mueller','Anna')})
    assert results['one']['token']==results['two']['token']
    assert all(value['status']==r.DEMO_HIGH for value in results.values())


def test_demographic_does_not_use_first_three_letters_or_initials():
    assert r.normalized_name('MARIA')!=r.normalized_name('MARIANNE')
    assert r.initial(profile(given='A.'))['status']==r.DEMO_AMBIGUOUS
    assert r.initial(profile(given='A. Maria'))['status']==r.DEMO_AMBIGUOUS
    assert r.initial(profile(surname='M.'))['status']==r.DEMO_AMBIGUOUS


def test_dob_alone_is_not_identity():
    p=r.new_profile();p['dobs'].add('1960-01-02')
    assert r.initial(p)['status']==r.NOT_FOUND
    assert r.initial(profile(surname='Unknown',given='Unknown'))['status']==r.NOT_FOUND


def test_contradictory_demographics_do_not_collapse():
    results=r.assign({'one':profile(sex='M'),'two':profile(sex='F')})
    assert all(value['identity_type']==r.UNRESOLVED for value in results.values())


def test_demographic_uniquely_attaches_to_validated_official_person():
    official=profile();value=svnr();add_id(official,value)
    results=r.assign({'official':official,'encounter':profile()})
    assert results['encounter']['identity_type']==r.DEMO
    assert results['encounter']['token']==results['official']['token']==(r.AT,value)
    secret=bytes(range(32))
    assert r.pseudonym(secret,results['encounter']['token'])==base.person_id(secret,value)


def test_two_valid_official_people_make_demographic_candidate_ambiguous():
    first,second=profile(),profile();add_id(first,svnr());add_id(second,svnr(start=200))
    results=r.assign({'one':first,'two':second,'unidentified':profile()})
    assert results['unidentified']['identity_type']==r.UNRESOLVED
    assert results['unidentified']['status']==r.DEMO_AMBIGUOUS


def test_official_conflicts_cannot_be_bypassed_using_demographics():
    conflict=profile();add_id(conflict,svnr());add_id(conflict,svnr(start=200))
    results=r.assign({'conflict':conflict,'other':profile()})
    assert results['conflict']['identity_type']==r.UNRESOLVED
    assert results['other']['identity_type']==r.UNRESOLVED


def test_unreadable_other_pdfs_do_not_reject_valid_identifier():
    p=profile();value=svnr();add_id(p,value)
    r.collect(dict(pages=[dict(text='')]),p,'image_only')
    assert r.initial(p)['identity_type']==r.AT


def test_foreign_id_never_encodes_or_requires_dob():
    p=r.new_profile();p['countries'].add('DE');add_id(p,'Z123456789')
    assert r.initial(p)['identity_type']==r.FOREIGN


def test_pseudonyms_use_separate_domains():
    key=bytes(range(32));value=svnr()
    at=r.pseudonym(key,(r.AT,value));foreign=r.pseudonym(key,(r.FOREIGN,'DE',value))
    demographic=r.pseudonym(key,(r.DEMO,'MUSTER','ANNA','1960-01-02'))
    assert len({at,foreign,demographic})==3
    assert all(x.startswith('P_') and len(x)==18 for x in [at,foreign,demographic])
