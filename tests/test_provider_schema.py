"""Provider schema compatibility without weakening local output bounds."""
import json
import pytest
from medcomply.providers import Inference, claude_schema
from medcomply.fixed_workflow import Judgement



def fake_inference(cls, provider, captured, findings=None):
    output = dict(decision='approve', summary='Offline schema test.', findings=findings or [],
                  evidence_cids=[], facts=[], policy_requirements=[])
    text = json.dumps(output)
    class Client:
        def post(self, provider, stage, payload, **kwargs):
            captured.append(payload)
            if provider == 'gemini':
                return dict(modelVersion='offline', candidates=[dict(content=dict(parts=[dict(text=text)]))])
            if provider == 'claude': return dict(model='offline', content=[dict(type='text', text=text)])
            return dict(model='offline', choices=[dict(message=dict(content=text))])
    obj = cls.__new__(cls); obj.live = True; obj.provider = provider; obj.client = Client()
    return obj




def test_claude_schema_removes_only_unsupported_wire_constraints():
    schema = Judgement.model_json_schema()
    original = json.dumps(schema, sort_keys=True)
    wire = claude_schema(schema)
    assert 'maxItems' not in json.dumps(wire)
    assert json.dumps(schema, sort_keys=True) == original
    assert schema['properties']['facts']['maxItems'] == 12
    assert wire['properties']['facts']['items'] == schema['properties']['facts']['items']


def test_claude_responses_still_obey_local_cardinality_bounds():
    captured = []
    obj = fake_inference(Inference, 'claude', captured, findings=['a', 'b', 'c', 'd'])
    with pytest.raises(ValueError): obj.invoke('Offline test.', {}, Judgement)
    assert 'maxItems' not in json.dumps(captured[0]['output_config']['format']['schema'])
