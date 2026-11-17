"""Offline control checks; live chain/model evidence is recorded separately."""
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from medcomply.quota import micrograms
from medcomply.reviews import integer,ReviewError
from medcomply.agents import decision_from_findings
from tools import provider_preflight as preflight

@pytest.mark.parametrize('value,unit,expected',[("0.000001","g",1),("0.125","mg",125),("5","kg",5_000_000_000),(5000,"g",5_000_000_000)])
def test_quantities_have_exact_units(value,unit,expected): assert micrograms(value,unit)==expected

@pytest.mark.parametrize('value,unit',[(True,'g'),(-1,'g'),(0,'g'),('NaN','g'),('Infinity','g'),('0.0000001','g'),('five','g'),(1,'lb')])
def test_unusable_quantity_never_becomes_an_allowance(value,unit):
    with pytest.raises(ValueError): micrograms(value,unit)

def test_review_arguments_do_not_accept_truth_values_or_floats():
    for value in (True,1.0,-1,9_000_000_000_000_000_001):
        with pytest.raises(ReviewError):integer(value)

def test_exceedance_cannot_be_overridden_by_a_clean_other_side():
    assert decision_from_findings([], [{'decision':'approve'},{'decision':'reject'}])=='reject'
    assert decision_from_findings(['MISSING_DOCUMENT'],[{'decision':'approve'}])=='review'

def test_transport_rejects_endpoint_override_for_another_provider(tmp_path):
    budget=preflight.Budget(tmp_path/'spend.sqlite3','test',250_000_000)
    client=preflight.Client({},budget,[])
    with pytest.raises(preflight.ProbeError):client.post('openai','test',{},native_gemini=True)
    assert budget.total()==0

def test_wallet_challenge_is_bound_to_its_exact_network_message():
    wallet=Account.create()
    message='MedComply\nSign in to sepolia (chain 11155111).\nNonce: unique-test'
    signature=wallet.sign_message(encode_defunct(text=message)).signature
    assert Account.recover_message(encode_defunct(text=message),signature=signature)==wallet.address
    assert Account.recover_message(encode_defunct(text=message.replace('sepolia','local')),signature=signature)!=wallet.address
