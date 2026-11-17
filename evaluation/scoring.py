"""Score SQL using additional records that distinguish all requested filters."""
import copy
from tools.benchmark_oracle import score, oracle, execute_query


def corrected_score(case,answer):
    if not score(case,answer):return False
    if case['task']!='database_query' or answer['decision']!='approve':return True
    where=case['input']['request']['where']
    variants=[]
    for field in ['country','substance','year','mode']:
        other={**where,'cap_ug':876543210,'used_ug':1234567,'reserved_ug':98765}
        other[field]=where[field]+1 if field=='year' else 'import' if field=='mode' and where[field]=='export' else 'export' if field=='mode' else str(where[field])+'-OTHER'
        variant=copy.deepcopy(case);variant['input']['records'].append(other);variants.append(variant)
    for variant in variants:
        try:
            observed=execute_query(variant,answer['sql']);expected=oracle(variant)['rows']
            if sorted(observed)!=sorted(expected):return False
        except Exception:return False
    return True
