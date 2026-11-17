"""Independent scoring rules; no imports of the application quota/agent code."""
from decimal import Decimal,InvalidOperation
import sqlite3

def quantity(value,unit):
    if isinstance(value,bool) or unit not in ('ug','mg','g','kg'):raise ValueError('Unusable quantity')
    n=Decimal(str(value))*{'ug':1,'mg':1000,'g':1_000_000,'kg':1_000_000_000}[unit]
    if not n.is_finite() or n<=0 or n!=int(n):raise ValueError('Unusable quantity')
    return int(n)

def oracle(case):
    task=case['task'];d=case['input']
    out={'decision':'review','value':None,'sql':None}
    try:
        if task=='quota_limits':
            if any(type(d[k]) is not int or d[k]<0 for k in ('cap_ug','used_ug','reserved_ug','declared_cap_ug','database_cap_ug')):return out
            if not d['cap_ug']==d['declared_cap_ug']==d['database_cap_ug']:return out
            q=quantity(d['shipment_quantity'],d['shipment_unit']);remaining=d['cap_ug']-d['used_ug']-d['reserved_ug']
            return {'decision':'approve' if q<=remaining else 'reject','value':remaining,'sql':None}
        if task=='quota_reconciliation':
            if d['history_complete'] is not True:return out
            events={}
            for event in d['events']:
                if any(event[k]!=v for k,v in d['key'].items()):continue
                if event['id'] in events and events[event['id']]!=event:return out
                events[event['id']]=event
            used=0
            for e in sorted(events.values(),key=lambda e:(e['block'],e['id'])):
                if e['kind']=='correction':
                    if e['before_ug']!=used or type(e['after_ug']) is not int or e['after_ug']<0:return out
                    used=e['after_ug']
                elif e['kind']=='consumed':
                    if type(e['quantity_ug']) is not int or e['quantity_ug']<=0:return out
                    used+=e['quantity_ug']
                else:return out
            decision='reject' if d['shipment_quantity_ug']>d['cap_ug']-used-d['reserved_ug'] else 'approve'
            if d['database_used_ug']!=used and decision=='approve':decision='review'
            return {'decision':decision,'value':used,'sql':None}
        if task=='database_query':
            filters=d['request']['where']
            if any(filters.get(k) is None for k in ('country','substance','year','mode')):return {**out,'rows':None}
            rows=[[r[k] for k in d['request']['select']] for r in d['records'] if all(r[k]==v for k,v in filters.items())]
            return {'decision':'approve','value':None,'sql':None,'rows':rows}
        if task=='cross_source':
            decision='approve' if d['on_chain']==d['shipment_document']==d['permit_document'] else 'review'
            return {'decision':decision,'value':None,'sql':None}
        if task=='permit_validity':
            p=d['permit'];s=d['shipment']
            if any(k not in p for k in ('expiry','approved','quantity_ug','shipment_id','issuer_country','unit')):return out
            if p['shipment_id']!=s['id'] or p['quantity_ug']!=s['quantity_ug'] or p['issuer_country']!=d['key']['country'] or p['unit']!='ug':return out
            return {'decision':'reject' if p['expiry']<d['now'] or not p['approved'] or not d['license_active'] else 'approve','value':None,'sql':None}
    except (KeyError,TypeError,ValueError,InvalidOperation):return out
    raise ValueError('Unknown task')

def execute_query(case,sql):
    if not isinstance(sql,str) or len(sql)>2000 or not sql.lstrip().lower().startswith('select '):raise ValueError('A single SELECT statement is required')
    db=sqlite3.connect(':memory:')
    try:
        db.execute('CREATE TABLE quotas(country TEXT, substance TEXT, year INTEGER, mode TEXT, cap_ug INTEGER, used_ug INTEGER, reserved_ug INTEGER)')
        for row in case['input']['records']:db.execute('INSERT INTO quotas VALUES (?,?,?,?,?,?,?)',[row[k] for k in ['country','substance','year','mode','cap_ug','used_ug','reserved_ug']])
        db.execute('PRAGMA query_only=ON')
        allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION}
        db.set_authorizer(lambda action,a,b,c,d:sqlite3.SQLITE_OK if action in allowed and (action!=sqlite3.SQLITE_READ or a=='quotas') else sqlite3.SQLITE_DENY)
        counter=[0]
        def bounded():counter[0]+=1;return int(counter[0]>100)
        db.set_progress_handler(bounded,100)
        cursor=db.execute(sql)
        if [c[0] for c in cursor.description]!=['cap_ug','used_ug','reserved_ug']:raise ValueError('Unexpected result columns')
        return [list(r) for r in cursor.fetchmany(100)]
    finally:db.close()

def score(case,answer):
    expected=oracle(case)
    if answer.get('decision')!=expected['decision'] or type(answer.get('value')) is not type(expected['value']) or answer.get('value')!=expected['value']:return False
    if case['task']=='database_query' and expected['decision']=='approve':
        try:return sorted(execute_query(case,answer.get('sql')))==sorted(expected['rows'])
        except (ValueError,sqlite3.Error):return False
    return answer.get('sql') is None
