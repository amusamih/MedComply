"""In-memory evidence adapter for offline workflow tests."""
import copy
import json
from types import SimpleNamespace
from medcomply.db import digest, quota_id


class Call:
    def __init__(self, fn): self.fn = fn
    def call(self): return self.fn()


class SnapshotChain:
    network = 'snapshot'
    def __init__(self, public):
        self.public = copy.deepcopy(public)
        self.ship = SimpleNamespace(address='0x' + '2' * 40)
        self.reg = SimpleNamespace(functions=SimpleNamespace(getActor=lambda account: Call(lambda: self.actor(account))))
        self.comp = SimpleNamespace(functions=SimpleNamespace(getIncbCapIpfs=lambda year: Call(lambda: self.public['declaration_cid'])))
        self.w3 = SimpleNamespace(eth=SimpleNamespace(get_block=lambda _: {'timestamp': self.public['now']}))
    def actor(self, account):
        v = self.identity(account)
        return [2 if v['country'] == 'USA' else 4, v['country'], v['licenseCID'], v['expiry'], v['licensed']]
    def identity(self, account):
        return next(copy.deepcopy(v) for v in self.public['identities'].values() if v['account'] == account)
    def shipment(self, sid): return copy.deepcopy(self.public['shipment'])
    def fingerprint(self, sid): return digest(self.public)
    def permit(self, pid, export): return copy.deepcopy(self.public['permits']['export' if export else 'import'])
    def quota(self, country, substance, year, mode): return copy.deepcopy(self.public['quota_contracts'][mode])
    def history(self, key): return copy.deepcopy(next(v for k, v in self.public['histories'].items() if 'quota-' + k == key))
    def document(self, cid):
        content = copy.deepcopy(self.public['documents'][cid])
        return dict(cid=cid, content=content, text=json.dumps(content, sort_keys=True))
    def session(self):
        rows = {}
        s = self.public['shipment']
        for mode, scopes in self.public['caches'].items():
            country = s['origin'] if mode == 'export' else s['destination']
            for scope, row in scopes.items():
                key = quota_id(country, s['substance'], s['year'], mode, scope, self.network + '@' + self.ship.address.lower())
                rows[key] = SimpleNamespace(**row)
        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def get(self, model, key): return rows.get(key)
        return Session()
