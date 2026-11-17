"""Regression checks for recommendations at the national/global review boundary."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from medcomply import quota
from medcomply.agents import VerificationWorkflow
from medcomply.db import quota_id


def quota_fixture(monkeypatch, *, national=100, global_cap=200, used=40, reserved=20, quantity=50):
    shipment={"id":9,"origin":"USA","destination":"GBR","substance":"morphine",
              "year":2025,"quantity":quantity,"state":"export_cleared"}
    contract={"key":"ledger-key","nationalCap":national,"globalCap":global_cap,
              "used":used,"reserved":reserved,"nationalSet":True,"globalSet":True}
    ledger={"used":used,"reserved":reserved,"complete":True,"events":[]}
    records={}
    for scope,cap in [("national",national),("global",global_cap)]:
        records[quota_id("USA","morphine",2025,"export",scope,"local@0xship")]=SimpleNamespace(
            cap=cap,used=used,reserved=reserved,block_number=10,block_hash="block-hash")
    @contextmanager
    def read_session():
        yield SimpleNamespace(get=lambda model,key:records.get(key))
    declaration={"year":2025,"unit":"ug","quotas":[{
        "country":"USA","substance":"morphine","mode":"export",
        "nationalCap":national,"globalCap":global_cap}]}
    monkeypatch.setattr(quota,"session",read_session)
    monkeypatch.setattr(quota,"Documents",lambda:SimpleNamespace(json=lambda cid:declaration))
    chain=SimpleNamespace(network="local",ship=SimpleNamespace(address="0xship"),
        quota=lambda *args:contract,history=lambda key:ledger,
        comp=SimpleNamespace(functions=SimpleNamespace(getIncbCapIpfs=lambda year:SimpleNamespace(call=lambda:"quota-cid"))))
    return chain,shipment,records,declaration


def test_global_assessment_rejects_when_only_national_allowance_is_exceeded(monkeypatch):
    chain,shipment,records,declaration=quota_fixture(monkeypatch)
    result=quota.check_quota(chain,shipment,"export","global")
    assert result["decision"]=="reject"
    assert result["remaining"]==40 and result["cap"]==100
    assert result["applicable_caps"]=={"national":100,"global":200}
    assert result["limit_basis"]=="minimum_of_national_and_global_caps"


def test_global_assessment_accepts_exact_remaining_allowance_including_reservations(monkeypatch):
    chain,shipment,records,declaration=quota_fixture(monkeypatch,quantity=40)
    assert quota.check_quota(chain,shipment,"export","global")["decision"]=="approve"


@pytest.mark.parametrize("source",["declaration","database"])
def test_global_assessment_checks_national_cap_integrity_too(monkeypatch,source):
    chain,shipment,records,declaration=quota_fixture(monkeypatch,quantity=10)
    if source=="declaration":declaration["quotas"][0]["nationalCap"]+=1
    else:records[quota_id("USA","morphine",2025,"export","national","local@0xship")].cap+=1
    result=quota.check_quota(chain,shipment,"export","global")
    assert result["decision"]=="review" and "CAP_INTEGRITY_FAILED" in result["findings"]


def test_stale_national_usage_requires_review_without_automatic_correction(monkeypatch):
    chain,shipment,records,declaration=quota_fixture(monkeypatch,quantity=10)
    row=records[quota_id("USA","morphine",2025,"export","national","local@0xship")]
    row.used=0
    result=quota.check_quota(chain,shipment,"export","global")
    assert result["decision"]=="review" and row.used==0
    assert result["correction"]["affected_scopes"]==["national"]
    assert result["correction"]["requires_human_approval"] is True


def final_recommendation(raw_decision, *, findings=None, quota_decision="approve", citations=None, fingerprint="unchanged"):
    chain=SimpleNamespace(fingerprint=lambda shipment_id:fingerprint)
    workflow=VerificationWorkflow(chain,None,"global")
    state={"documents":{"shipment":{"cid":"supplied-cid"}},"findings":findings or [],
           "quota_assessments":[{"decision":quota_decision}],
           "raw":{"output":{"decision":raw_decision,"summary":"Model explanation.",
                            "findings":["Supplied policy condition."],"evidence_cids":citations or []}},
           "evidence":{"shipment_id":9},"shipment":{"id":9},"initial_fingerprint":"unchanged"}
    return workflow.finalize(state)["result"]


def test_model_policy_rejection_remains_a_recommendation_for_human_review():
    result=final_recommendation("reject")
    assert result["decision"]=="reject" and result["decision_basis"]=="model_policy_assessment"
    assert result["requires_human_decision"] is True
    assert result["model_findings"]==["Supplied policy condition."]


@pytest.mark.parametrize("raw",["approve","reject","review"])
def test_missing_evidence_is_not_overruled_by_model_classification(raw):
    assert final_recommendation(raw,findings=["DOCUMENT_MISSING"])["decision"]=="review"


@pytest.mark.parametrize("raw",["approve","reject","review"])
def test_verified_quota_exceedance_is_not_overruled_by_model_classification(raw):
    assert final_recommendation(raw,quota_decision="reject")["decision"]=="reject"


def test_unsupported_model_citation_prevents_approval():
    result=final_recommendation("approve",citations=["invented-cid"])
    assert result["decision"]=="review" and "UNSUPPORTED_MODEL_CITATION" in result["findings"]
    assert result["summary"]!="Model explanation."


def test_changed_evidence_invalidates_recommendation_and_summary():
    result=final_recommendation("approve",fingerprint="changed")
    assert result["decision"]=="review" and result["decision_basis"]=="evidence_changed"
    assert "fresh assessment" in result["summary"]
