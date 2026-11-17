from __future__ import annotations
import json
import hashlib
from pathlib import Path
from web3 import Web3
from .config import ROOT, manifest, rpc_url
from .db import digest

ROLE_NAMES = ["unregistered", "customs", "exporter", "logistics", "importer"]
STATE_NAMES = ["missing", "created", "export_cleared", "approved", "in_transit", "import_cleared", "received", "cancelled"]
SHIPMENT_FIELDS = ["exporter","importer","logistics","origin","destination","substance","quantity","year","documentCID","exportPermit","importPermit","state"]

def serial(value):
    if isinstance(value, bytes): return "0x" + value.hex()
    if isinstance(value, dict) or hasattr(value,"items"): return {str(k):serial(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)): return [serial(v) for v in value]
    return value

def country(value):
    if isinstance(value,str) and len(value) == 3 and value.isupper() and value.isascii(): return value.encode()
    raise ValueError("Use a three-letter uppercase country code")

def substance(value):
    if not isinstance(value,str) or not value.isascii() or not 0 < len(value) <= 31:
        raise ValueError("Invalid substance code")
    return value.encode().ljust(32,b"\0")

class Chain:
    def __init__(self, network="local"):
        self.network = network
        self.deployment = manifest(network)
        self.w3 = Web3(Web3.HTTPProvider(rpc_url(network), request_kwargs={"timeout":20}))
        expected = 31337 if network == "local" else 11155111
        if self.w3.eth.chain_id != expected or self.deployment["chain_id"] != expected:
            raise ValueError("Chain ID mismatch; mainnet is not supported")
        self.contracts = {}
        for name in ("Registration", "Compliance", "Shipping"):
            artifact = json.loads((ROOT / "contracts" / "out" / (name + ".sol") / (name + ".json")).read_text())
            address = self.deployment["contracts"][name]
            code=self.w3.eth.get_code(address)
            if not code: raise ValueError("Deployment code is missing")
            if hashlib.sha256((ROOT/"contracts/src"/(name+".sol")).read_bytes()).hexdigest()!=self.deployment["source_sha256"][name]: raise ValueError("Deployment source manifest differs from current source")
            expected_hash=self.deployment.get("runtime_sha256",{}).get(name)
            if expected_hash and hashlib.sha256(code).hexdigest()!=expected_hash: raise ValueError("Runtime code differs from deployment manifest")
            self.contracts[name] = self.w3.eth.contract(address=address, abi=artifact["abi"])
        self.reg, self.comp, self.ship = [self.contracts[n] for n in ("Registration","Compliance","Shipping")]
        self._consumption_logs=None

    def logs(self,contract,event_name,start=None,end=None,argument_filters=None):
        """Read the complete requested range within the configured RPC's block limit."""
        start=self.deployment["deployment_block"] if start is None else start
        end=self.w3.eth.block_number if end is None else end
        result=[];width=10 if self.network=="sepolia" else 2000
        event=getattr(self.contracts[contract].events,event_name)
        for lo in range(start,end+1,width):
            result.extend(event().get_logs(from_block=lo,to_block=min(lo+width-1,end),argument_filters=argument_filters))
        return result

    def identity(self, account):
        account = Web3.to_checksum_address(account)
        if account == self.reg.functions.INCB().call(): return {"account":account,"role":"incb","country":None}
        nca = self.reg.functions.authorities(account).call()
        if nca[1]: return {"account":account,"role":"nca","country":nca[0].decode()}
        a = self.reg.functions.getActor(account).call()
        licensed = a[4] and a[3] >= self.w3.eth.get_block("latest")["timestamp"]
        return {"account":account, "role":ROLE_NAMES[a[0]] if licensed else "unregistered",
                "country":a[1].decode().strip("\0"), "licensed":licensed,
                "licenseCID":a[2],"expiry":a[3]}

    def shipment(self, shipment_id, block="latest"):
        s = dict(zip(SHIPMENT_FIELDS, self.ship.functions.getShipment(shipment_id).call(block_identifier=block)))
        s.update({"id":shipment_id,"origin":s["origin"].decode(), "destination":s["destination"].decode(),
                  "substance":s["substance"].decode().strip("\0"), "unit":"ug", "state":STATE_NAMES[s["state"]]})
        return s

    def permit(self, permit_id, is_export, block="latest"):
        p = self.comp.functions.getPermit(permit_id,is_export).call(block_identifier=block)
        return dict(zip(["shipmentId","expiry","documentCID","status","reviewHash"],serial(p)))

    def quota(self, country_code, substance_code, year, mode, block="latest"):
        key = self.ship.functions.quotaKey(country(country_code),substance(substance_code),year,mode=="export").call()
        q = self.ship.functions.quotas(key).call(block_identifier=block)
        return {"key":serial(key),**dict(zip(["nationalCap","globalCap","used","reserved","nationalSet","globalSet"],q))}

    def history(self, quota_key, end_block=None):
        """Rebuild usage and reservations from Shipping events, including replacement corrections."""
        end = self.w3.eth.block_number if end_block is None else end_block
        start = self.deployment["deployment_block"]
        if end < start: raise ValueError("History ends before deployment")
        # Fetch all four event signatures in one query per range, then decode.
        names=("QuotaUsageCorrected","QuotaReserved","QuotaReleased","QuotaConsumed")
        decoders={}
        for name in names:
            event=getattr(self.ship.events,name)()
            signature=name+'('+','.join(i['type'] for i in event.abi['inputs'])+')'
            decoders[Web3.keccak(text=signature).to_0x_hex()]=(name,event)
        if self._consumption_logs is None or self._consumption_logs['end']!=end:
            collected=[];width=10 if self.network=="sepolia" else 2000
            for lo in range(start,end+1,width):
                raw=self.w3.eth.get_logs({'address':self.ship.address,'fromBlock':lo,'toBlock':min(lo+width-1,end),'topics':[list(decoders)]})
                for log in raw:
                    name,decoder=decoders[log['topics'][0].to_0x_hex()]
                    collected.append({'name':name,**serial(dict(decoder.process_log(log)))})
            self._consumption_logs={'end':end,'events':collected}
        events=[e for e in self._consumption_logs['events'] if e['args']['key']==quota_key]
        unique = {}
        for event in events:
            key = (event["transactionHash"],event["logIndex"])
            if key in unique and unique[key] != event: raise ValueError("Conflicting duplicated event")
            unique[key] = event
        ordered = sorted(unique.values(),key=lambda e:(e["blockNumber"],e["transactionIndex"],e["logIndex"]))
        used, reserved = 0, 0
        for event in ordered:
            canonical = serial(self.w3.eth.get_block(event["blockNumber"])["hash"])
            if canonical != event["blockHash"]: raise ValueError("Reorganization detected; history must be reread")
            args = event["args"]
            if event["name"] == "QuotaUsageCorrected":
                if args["beforeUsage"] != used: raise ValueError("Incomplete correction history")
                used = args["afterUsage"]
            elif event["name"] == "QuotaReserved": reserved += args["quantity"]
            elif event["name"] == "QuotaReleased": reserved -= args["quantity"]
            else: reserved -= args["quantity"]; used += args["quantity"]
            if used < 0 or reserved < 0: raise ValueError("Incomplete reservation history")
        return {"used":used,"reserved":reserved,"events":ordered,"from_block":start,"to_block":end,
                "block_hash":serial(self.w3.eth.get_block(end)["hash"]), "complete":True}

    def fingerprint(self, shipment_id):
        s = self.shipment(shipment_id)
        return digest({"chain_id":self.w3.eth.chain_id,"contracts":self.deployment["contracts"],"shipment":s,
                       "party_records":{a:serial(self.reg.functions.getActor(a).call()) for a in (s["exporter"],s["importer"],s["logistics"])},
                       "quota_document":self.comp.functions.getIncbCapIpfs(s["year"]).call(),
                       "export_permit":self.permit(s["exportPermit"],True),
                       "import_permit":self.permit(s["importPermit"],False),
                       "export_quota":self.quota(s["origin"],s["substance"],s["year"],"export"),
                       "import_quota":self.quota(s["destination"],s["substance"],s["year"],"import")})

    def transaction(self, contract, function, args, account):
        account = Web3.to_checksum_address(account)
        fn = getattr(self.contracts[contract].functions,function)(*args)
        tx = fn.build_transaction({"from":account,"nonce":self.w3.eth.get_transaction_count(account,"pending")})
        return serial(tx)

    def submit_local(self, tx):
        if self.network != "local" or self.w3.eth.chain_id != 31337: raise ValueError("Local submission only")
        tx = dict(tx)
        tx.pop("chainId",None)
        receipt = self.w3.eth.wait_for_transaction_receipt(self.w3.eth.send_transaction(tx),timeout=60)
        if receipt["status"] != 1: raise ValueError("Transaction reverted")
        return serial(dict(receipt))
