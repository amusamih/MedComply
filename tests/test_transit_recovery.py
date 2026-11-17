"""Offline application checks; scripted model responses are not accuracy measurements."""
import copy
from types import SimpleNamespace
import pytest
from medcomply import agents, quota
from medcomply.fixed_workflow import VerificationWorkflow
from tests.scenarios import build_case
from tests.snapshot import SnapshotChain


@pytest.fixture
def recovery(monkeypatch):
    public = build_case('clean', 'national_import')['public']
    s = public['shipment']; s['state'] = 'in_transit'
    public['permits']['import'].update(status=3)
    q = public['quota_contracts']['import']
    # The reservation fills the remaining national allowance: allocating again
    # would reject an otherwise valid permit replacement.
    q['reserved'] = q['nationalCap'] - q['used']
    h = public['histories']['import']
    h.update(used=q['used'], reserved=q['reserved'], complete=True,
             events=[{'name':'QuotaReserved', 'args':{'shipmentId':s['id'], 'quantity':s['quantity']}}])
    for cache in public['caches']['import'].values():
        cache.update(used=q['used'], reserved=q['reserved'])
    chain = SnapshotChain(public)
    monkeypatch.setattr(agents, 'get_document', chain.document)
    monkeypatch.setattr(quota, 'Documents', lambda:SimpleNamespace(json=lambda cid:chain.public['documents'][cid]))
    monkeypatch.setattr(quota, 'session', chain.session)
    return chain


def check(chain):
    return quota.check_quota(chain, chain.shipment(1), 'import', 'national', reserved_import=True)


def test_replacement_audits_existing_reservation_without_allocating_twice(recovery):
    before = copy.deepcopy(recovery.public)
    result = check(recovery)
    assert result['decision'] == 'approve' and result['remaining'] == 0
    assert result['shipment_reserved'] == before['shipment']['quantity']
    assert result['assessment_purpose'] == 'import_permit_replacement'
    assert result['applicable_caps'] == {'national':30_000_000_000, 'global':40_000_000_000}
    assert recovery.public == before


@pytest.mark.parametrize('mutation', ['missing','other_shipment','wrong_quantity','consumed','released','duplicate','incomplete','aggregate_shortfall'])
def test_replacement_cannot_approve_without_its_exact_outstanding_reservation(recovery, mutation):
    p = recovery.public; h = p['histories']['import']; s = p['shipment']
    if mutation == 'missing': h['events'] = []
    if mutation == 'other_shipment': h['events'][0]['args']['shipmentId'] += 1
    if mutation == 'wrong_quantity': h['events'][0]['args']['quantity'] += 1
    if mutation in ('consumed', 'released'):
        h['events'].append({'name':'QuotaConsumed' if mutation == 'consumed' else 'QuotaReleased',
                            'args':{'shipmentId':s['id'], 'quantity':s['quantity']}})
    if mutation == 'duplicate': h['events'] *= 2
    if mutation == 'incomplete': h['complete'] = False
    if mutation == 'aggregate_shortfall':
        p['quota_contracts']['import']['reserved'] = h['reserved'] = s['quantity'] - 1
        for cache in p['caches']['import'].values(): cache['reserved'] = h['reserved']
    assert check(recovery)['decision'] == 'review'


@pytest.mark.parametrize('scope', ['national','global'])
def test_stale_quota_cache_requires_human_review_during_replacement(recovery, scope):
    recovery.public['caches']['import'][scope]['used'] -= 1
    result = check(recovery)
    assert result['decision'] == 'review' and 'STALE_DATABASE_USAGE' in result['findings']
    assert recovery.public['caches']['import'][scope]['used'] == result['used'] - 1


def test_standard_quota_allocation_remains_unavailable_after_pickup(recovery):
    result = quota.check_quota(recovery, recovery.shipment(1), 'import', 'national')
    assert result['decision'] == 'review'
    assert 'SHIPMENT_ALREADY_COMMITTED_OR_CLOSED' in result['findings']


@pytest.mark.parametrize('state,mode,scope', [('created','import','national'),('received','import','national'),
                                            ('in_transit','export','national'),('in_transit','import','global')])
def test_reservation_audit_cannot_be_reused_for_initial_or_export_approval(recovery,state,mode,scope):
    s = recovery.shipment(1); s['state'] = state
    with pytest.raises(ValueError): quota.check_quota(recovery,s,mode,scope,reserved_import=True)


@pytest.mark.parametrize('change,issue', [('valid_permit','IMPORT_PERMIT_STILL_VALID'),
                                        ('unlicensed','UNLICENSED_PARTY:importer'),
                                        ('country','PARTY_JURISDICTION_CHANGED:importer'),
                                        ('wrong_shipment','REPLACEMENT_IMPORT_PERMIT_UNVERIFIED')])
def test_replacement_assessment_checks_eligibility_and_current_participant_records(recovery,change,issue):
    p = recovery.public
    if change == 'valid_permit': p['permits']['import']['status'] = 2
    if change == 'unlicensed': p['identities']['importer'].update(licensed=False,role='unregistered')
    if change == 'country': p['identities']['importer']['country'] = 'USA'
    if change == 'wrong_shipment': p['permits']['import']['shipmentId'] += 1
    state = VerificationWorkflow(recovery,None,'national','import').gather({'shipment':recovery.shipment(1)})
    assert issue in state['findings']
    assert state['evidence']['previous_import_permit'] == p['permits']['import']


@pytest.mark.parametrize('expired', [False, True])
def test_complete_scripted_replacement_assessment_supports_a_fresh_reviewed_draft(recovery, expired):
    if expired:
        recovery.public['permits']['import'].update(status=2,expiry=recovery.public['now'] - 1)
    class Scripted:
        calls = 0
        def invoke(self, system, evidence, schema, stage, **kwargs):
            self.calls += 1
            sources = evidence['sources']
            facts = []
            for f in evidence['required_approval_facts']:
                value = sources[f['source_id']]
                for key in f['path'][1:].split('/'): value = value[key]
                facts.append({**f, 'value':value})
            return {'output':{'decision':'approve','summary':'Current licenses and shipment evidence support review of a replacement permit using the existing reservation.',
                              'findings':[], 'evidence_cids':[d['cid'] for d in evidence['documents'].values()] + [recovery.public['declaration_cid']],
                              'facts':facts,'policy_requirements':list(range(1,len(evidence['policy']['requirements'])+1))}}
    scripted = Scripted()
    result = VerificationWorkflow(recovery,scripted,'national','import').invoke(recovery.public['shipment']['id'])
    assert result['decision'] == 'approve', result
    assert result['requires_human_decision'] is True
    assert 'replacement' in result['evidence']['assessment_stage']
    assert result['quota_assessments'][0]['shipment_reserved'] == recovery.public['shipment']['quantity']
    assert result['state_fingerprint'] == recovery.fingerprint(1)
    assert scripted.calls == 1
