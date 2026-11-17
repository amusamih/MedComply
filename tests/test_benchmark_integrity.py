import copy
import pytest
from tools.benchmark_data import generate
from tools.benchmark_oracle import oracle,execute_query,score
from medcomply.task_controls import controlled

def test_generated_labels_match_independent_rules_for_every_case():
    for split,count in [('development',20),('test',300)]:
        rows=generate(split,count)
        assert len(rows)==5*count
        for row in rows:assert oracle(row)==row['expected'],row['id']
        for row in rows:assert score(row,controlled(row['task'],row['input'])),row['id']

def test_query_scoring_requires_correct_country_year_and_side():
    case=next(c for c in generate('development',20) if c['task']=='database_query' and not c['edge_case'])
    k=case['input']['request']['where']
    query="SELECT cap_ug,used_ug,reserved_ug FROM quotas WHERE country='{}' AND substance='{}' AND year={} AND mode='{}'".format(k['country'],k['substance'],k['year'],k['mode'])
    assert score(case,{'decision':'approve','value':None,'sql':query})
    assert not score(case,{'decision':'approve','value':None,'sql':'SELECT cap_ug,used_ug,reserved_ug FROM quotas'})
    with pytest.raises(ValueError):execute_query(case,'DELETE FROM quotas')
    with pytest.raises(Exception):execute_query(case,query+'; DROP TABLE quotas')

def test_duplicate_events_never_inflate_usage_and_conflicts_require_review():
    case=next(c for c in generate('development',20) if c['task']=='quota_reconciliation' and c['subtype']==0)
    original=oracle(case); changed=copy.deepcopy(case);changed['input']['events']+=changed['input']['events']
    assert oracle(changed)==original
    changed['input']['events'][-1]=dict(changed['input']['events'][-1],quantity_ug=1)
    assert oracle(changed)['decision']=='review'
