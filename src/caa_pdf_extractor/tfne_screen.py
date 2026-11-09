"""Transparent operational research screen; independent of diagnosis, EEG and imaging."""
VERSION='TFNE_SCREEN_0.1'


def derive(episode):
    course=episode['temporal_course'];domains=set(episode['symptom_domains'])
    resolved=episode['fully_resolved']=='TRUE';focal=episode['focality']=='FOCAL'
    mixed_polarity=bool(episode['positive_symptoms'] and episode['negative_symptoms'])
    screen='TFNE_SCREEN_UNCLASSIFIABLE';phenotype='UNCLASSIFIABLE';categories=[];reasons=[]
    positive_assertion=episode['assertion'] in ('ESTABLISHED','HISTORICAL')
    if not positive_assertion:reasons.append('Episode assertion does not establish an actual event')
    elif course in ('PERSISTENT','PROGRESSIVE','MIXED','FLUCTUATING_WITHOUT_CLEAR_RESOLUTION'):
        screen='TFNE_SCREEN_NEGATIVE';reasons.append('No fully resolved focal episode established at episode level')
        if course=='MIXED':categories.append('MIXED_TRANSIENT_PERSISTENT_DEFICIT')
        elif course=='PROGRESSIVE' and 'COGNITIVE' in domains:phenotype='PROGRESSIVE_COGNITIVE';categories.append('PROGRESSIVE_COGNITIVE_DEFICIT')
        elif focal:phenotype='PERSISTENT_FOCAL';categories.append('PERSISTENT_FOCAL_NEUROLOGICAL_DEFICIT')
        elif course!='FLUCTUATING_WITHOUT_CLEAR_RESOLUTION':phenotype='PERSISTENT_OTHER';categories.append('PERSISTENT_OTHER_NEUROLOGICAL_DEFICIT')
    elif course=='TRANSIENT_FULLY_RESOLVED' and resolved:
        maximum=episode.get('duration_max_minutes');minimum=episode.get('duration_min_minutes')
        if minimum is not None and minimum>1440 or maximum is not None and maximum>1440:
            screen='TFNE_SCREEN_NEGATIVE';reasons.append('Outside the pilot operational duration bound of 24 hours; episode retained')
        elif episode['focality']=='NONFOCAL':
            screen='TFNE_SCREEN_NEGATIVE';phenotype='TRANSIENT_ATYPICAL';categories.append('TRANSIENT_ATYPICAL_CAA_SYMPTOM')
        elif focal and domains&{'SENSORY','MOTOR','LANGUAGE','VISUAL'}:
            strong=episode['spreading_progression']=='TRUE' or (episode['recurrent']=='TRUE' and episode['stereotyped']=='TRUE') or mixed_polarity
            screen='TFNE_SCREEN_STRONG' if strong else 'TFNE_SCREEN_POSSIBLE'
            phenotype='TFNE_TYPICAL' if strong else 'TFNE_POSSIBLE'
            reasons.append('Fully resolved focal symptoms'+('; high-value feature documented' if strong else '; limited supportive phenomenology'))
    temporal_axis={'TRANSIENT_FULLY_RESOLVED':'TRANSIENT','PERSISTENT':'PERSISTENT','PROGRESSIVE':'PROGRESSIVE','MIXED':'MIXED'}.get(course,'UNKNOWN')
    lo,hi=episode.get('duration_min_minutes'),episode.get('duration_max_minutes');duration_bin='UNKNOWN'
    if lo is not None and hi is not None:
        for name,a,b in [('<1 min',0,1),('1–5 min',1,6),('6–30 min',6,31),('31–60 min',31,61),('1–3 h',61,181),('3–24 h',181,1441),('>24 h',1441,float('inf'))]:
            if a<=lo and hi<b:duration_bin=name;break
    return dict(TFNE_clinical_screen=screen,temporal_axis=temporal_axis,retrospective_clinical_phenotype=phenotype,research_categories=categories,
        mixed_positive_negative_symptoms=mixed_polarity,positive_negative='MIXED' if mixed_polarity else 'POSITIVE' if episode['positive_symptoms'] else 'NEGATIVE' if episode['negative_symptoms'] else 'UNKNOWN',
        duration_bin=duration_bin,screen_rule_version=VERSION,screen_rule_reasons=reasons,
        screen_derived_from=['assertion','temporal_course','fully_resolved','focality','symptom_domains','duration_min_minutes','duration_max_minutes','spreading_progression','recurrent','stereotyped','positive_symptoms','negative_symptoms'])
