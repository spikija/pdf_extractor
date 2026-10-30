"""CHA2DS2-VA bounds; unknown is never an absent patient fact."""
KEYS=("C","H","A2","D","S2","V","A")
WEIGHTS=dict(C=1,H=1,A2=2,D=1,S2=2,V=1,A=1)
VERSION="CHA2DS2_VA_BOUNDS_V1"
def minimum_score(parts):
    """Bounds respect mutual exclusion of the two age categories."""
    low = sum(v for v in parts.values() if v is not None)
    missing = [k for k in KEYS if parts.get(k) is None]
    high = low + sum(WEIGHTS[k] for k in missing if k not in ('A2','A'))
    if parts.get('A2') is None and parts.get('A') is None: high += 2
    elif parts.get('A2') is None: high += 0 if parts.get('A') == 1 else 2
    elif parts.get('A') is None: high += 0 if parts.get('A2') == 2 else 1
    complete = not missing
    exact = low if complete else None
    rec = ('LOW_RISK' if exact==0 else 'CONSIDER_OAC' if exact==1 else 'OAC_RECOMMENDED') if complete else 'OAC_RECOMMENDED_FROM_MINIMUM_CONFIRMED_SCORE' if low>=2 else 'RECOMMENDATION_RANGE_UNCERTAIN'
    return dict(cha2ds2_va_exact=exact, cha2ds2_va_minimum_confirmed=low,
        cha2ds2_va_maximum_possible=high, score_range=[low,high], score_complete=complete,
        missing_components=missing, esc_oac_recommendation=rec)
