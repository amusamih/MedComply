"""Deterministic controls for the versioned component benchmark.

These are reported separately from raw model accuracy. They are not an LLM.
Operational chain evidence remains handled by chain.py/quota.py/agents.py.
"""
from .quota import micrograms

def controlled(task,data):
    result={'decision':'review','value':None,'sql':None}
    try:
        if task=='quota_limits':
            cap,used,reserved=(data[k] for k in ['cap_ug','used_ug','reserved_ug'])
            if any(type(v) is not int or v<0 for v in [cap,used,reserved,data['declared_cap_ug'],data['database_cap_ug']]):return result
            if len({cap,data['declared_cap_ug'],data['database_cap_ug']})!=1:return result
            amount=micrograms(data['shipment_quantity'],data['shipment_unit']);result['value']=cap-used-reserved
            result['decision']='reject' if amount>result['value'] else 'approve'
        elif task=='quota_reconciliation':
            if not data['history_complete']:return result
            logs={}
            for event in data['events']:
                if any(event.get(k)!=data['key'][k] for k in ['country','substance','year','mode']):continue
                prior=logs.get(event['id'])
                if prior is not None and prior!=event:return result
                logs[event['id']]=event
            usage=0
            for event in sorted(logs.values(),key=lambda r:(r['block'],r['id'])):
                if event['kind']=='consumed':
                    if type(event['quantity_ug']) is not int:return result
                    usage+=micrograms(event['quantity_ug'],'ug')
                elif event['kind']=='correction':
                    if event['before_ug']!=usage or type(event['after_ug']) is not int or event['after_ug']<0:return result
                    usage=event['after_ug']
                else:return result
            result['value']=usage
            result['decision']='reject' if data['shipment_quantity_ug']>data['cap_ug']-usage-data['reserved_ug'] else 'review' if usage!=data['database_used_ug'] else 'approve'
        elif task=='database_query':
            request=data['request']['where'];fields=['country','substance','year','mode']
            if any(request.get(k) is None for k in fields):return result
            def literal(v):return str(v) if type(v) is int else "'"+str(v).replace("'","''")+"'"
            result.update(decision='approve',sql='SELECT cap_ug,used_ug,reserved_ug FROM quotas WHERE '+' AND '.join(k+'='+literal(request[k]) for k in fields))
        elif task=='cross_source':
            result['decision']='approve' if data['on_chain']==data['shipment_document']==data['permit_document'] else 'review'
        elif task=='permit_validity':
            p=data['permit'];s=data['shipment']
            if not {'approved','expiry','shipment_id','issuer_country','quantity_ug','unit'}<=p.keys():return result
            if p['shipment_id']!=s['id'] or p['quantity_ug']!=s['quantity_ug'] or p['issuer_country']!=data['key']['country'] or p['unit']!='ug':return result
            result['decision']='approve' if p['approved'] and data['license_active'] and p['expiry']>=data['now'] else 'reject'
        else:raise ValueError('Unsupported control task')
    except (KeyError,TypeError,ValueError):return {'decision':'review','value':None,'sql':None}
    return result
