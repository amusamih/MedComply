"""Fixture adapter integrity; these are not model-accuracy observations."""
from unittest.mock import patch
from types import SimpleNamespace
import pytest
from medcomply import agents, quota
from medcomply.fixed_workflow import VerificationWorkflow, source_package
from tests.scenarios import build, build_case
from tests.snapshot import SnapshotChain


def context(chain):
    return (patch.object(agents, 'get_document', chain.document),
            patch.object(quota, 'Documents', lambda: SimpleNamespace(json=lambda cid: chain.public['documents'][cid])),
            patch.object(quota, 'session', chain.session))


def test_fresh_cases_have_private_labels_and_no_label_metadata_in_inputs():
    cases = build()
    assert len(cases) == 60 and len({c['id'] for c in cases}) == 60
    assert {c['stage'] for c in cases} == {'national_import', 'national_export', 'global'}
    for c in cases:
        assert not {'private', 'expected', 'family', 'reason'} & set(c['public'])


def test_snapshot_adapter_exercises_real_quota_calculation_and_conflicts():
    for family, decision in [('clean', 'approve'), ('exact_boundary', 'approve'),
                             ('equivalent_units', 'approve'), ('existing_reservations', 'approve'),
                             ('quota_exceeded', 'reject'), ('reservations_exceed_allowance', 'reject'),
                             ('stale_cache', 'review'), ('cap_disagreement', 'review'),
                             ('history_disagreement', 'review')]:
        chain = SnapshotChain(build_case(family, 'global')['public'])
        a, b, c = context(chain)
        with a, b, c:
            for mode in ('import', 'export'):
                result = quota.check_quota(chain, chain.shipment(1), mode, 'global')
                assert result['decision'] == decision, (family, result)
                if family == 'exact_boundary': assert result['quantity'] == result['remaining']


def test_snapshot_document_units_and_stage_specific_permit_requirements():
    for stage in ('national_import', 'national_export', 'global'):
        for family in ('clean', 'equivalent_units', 'document_identity_conflict', 'unavailable_document'):
            chain = SnapshotChain(build_case(family, stage)['public'])
            workflow = VerificationWorkflow(chain, None, 'global' if stage == 'global' else 'national',
                                            'import' if stage == 'national_import' else 'export')
            a, b, c = context(chain)
            with a, b, c:
                state = workflow.gather({'shipment': chain.shipment(1)})
                if family in ('clean', 'equivalent_units'):
                    assert not state['findings'], (family, stage, state['findings'])
                if family == 'unavailable_document':
                    assert any('DOCUMENT_RETRIEVAL_FAILED' in f for f in state['findings'])
                state['quota_assessments'] = []
                assert 'sources' in source_package(state)


@pytest.mark.parametrize('observed', [None, True, '1', 2, 1])
def test_final_gather_binds_typed_shipment_document_identifier(observed):
    from tests.scenarios import cid
    public = build_case('clean', 'national_import')['public']
    public['shipment']['id'] = 1
    doc = public['documents'].pop(public['shipment']['documentCID'])
    doc['shipment_id'] = observed
    reference = cid(doc); public['documents'][reference] = doc
    public['shipment']['documentCID'] = reference
    chain = SnapshotChain(public)
    workflow = VerificationWorkflow(chain, None, 'national', 'import')
    a, b, c = context(chain)
    with a, b, c: state = workflow.gather({'shipment': chain.shipment(1)})
    issue = 'SHIPMENT_DOCUMENT_CONFLICT:shipment_id'
    assert (issue in state['findings']) == (type(observed) is not int or observed != 1)
    assert state['evidence']['deterministic_findings'] == state['findings']
