from tools.benchmark_data import generate
from tools.benchmark_oracle import score
from evaluation.scoring import corrected_score

def test_correct_result_on_one_fixture_does_not_hide_omitted_filters():
    case=next(c for c in generate('development',20) if c['task']=='database_query' and not c['edge_case'])
    country=case['input']['request']['where']['country']
    answer={'decision':'approve','value':None,'sql':"SELECT cap_ug,used_ug,reserved_ug FROM quotas WHERE country='"+country+"'"}
    assert score(case,answer)
    assert not corrected_score(case,answer)
