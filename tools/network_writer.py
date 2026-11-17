"""Durable, test-network-only signing for explicitly authorized fixture scripts.

The web app never imports this module. Role keys remain in ignored local storage.
"""
from __future__ import annotations
import json
from eth_account import Account
from web3 import Web3
from medcomply.config import LOCAL,ROOT,environment
from medcomply.chain import serial
from medcomply.db import digest

ROLE_NAMES=["nca_usa","nca_gbr","exporter","importer","logistics","customs_usa","customs_gbr"]

def role_signers(create=False):
    deployer=Account.from_key(environment()["SEPOLIA_PRIVATE_KEY"])
    path=LOCAL/"sepolia-actors.json"
    if not path.exists():
        if not create: raise ValueError("Create the local Sepolia role accounts first")
        records={}
        for name in ROLE_NAMES:
            a=Account.create(); records[name]={"address":a.address,"private_key":a.key.to_0x_hex()}
        path.write_text(json.dumps(records,indent=2)+"\n")
    records=json.loads(path.read_text())
    signers={name:Account.from_key(records[name]["private_key"]) for name in ROLE_NAMES}
    if any(signers[n].address!=records[n]["address"] for n in ROLE_NAMES): raise ValueError("Role key/address mismatch")
    signers["incb"]=deployer
    if len({a.address for a in signers.values()})!=8: raise ValueError("Eight distinct test accounts are required")
    return signers

class Writer:
    def __init__(self,w3,network,journal,create_roles=False):
        self.w3,self.network=w3,network
        self.chain_id=31337 if network=="local" else 11155111
        if w3.eth.chain_id!=self.chain_id: raise ValueError("Unsupported chain ID; mainnet is forbidden")
        self.signers=role_signers(create_roles) if network=="sepolia" else {}
        self.by_address={a.address.lower():a for a in self.signers.values()}
        self.path=LOCAL/(journal+".json")
        self.data=json.loads(self.path.read_text()) if self.path.exists() else {"network":network,"transactions":{}}
    def save(self): self.path.write_text(json.dumps(self.data,indent=2)+"\n")
    def send(self,label,build,sender,binding):
        key=digest(serial(binding)); stored=self.data["transactions"].get(label)
        if stored:
            if stored["binding"]!=key: raise ValueError("Journal action differs from the recorded request")
            tx_hash=stored["hash"]
            if stored.get("receipt"):
                r=self.w3.eth.get_transaction_receipt(tx_hash)
                if serial(r["blockHash"])!=stored["receipt"]["blockHash"]: raise ValueError("Journal receipt was reorganized")
                if r["status"]!=1: raise ValueError("Recorded transaction reverted; use a distinct reviewed retry label")
                return serial(dict(r))
        else:
            tx=build({"from":sender,"nonce":self.w3.eth.get_transaction_count(sender,"pending"),"chainId":self.chain_id})
            if self.network=="sepolia":
                # Bound all fixture fees; do not consume arbitrary test ETH in a spike.
                fee=tx.get("maxFeePerGas",tx.get("gasPrice",0))
                if fee>Web3.to_wei(50,"gwei"): raise ValueError("Test-network gas price exceeds the configured cap")
                if self.w3.eth.get_balance(sender)<tx["gas"]*fee+tx.get("value",0): raise ValueError("Test wallet has insufficient ETH for the estimated transaction")
                signed=self.by_address[sender.lower()].sign_transaction(tx)
                tx_hash=signed.hash.to_0x_hex();raw=signed.raw_transaction.to_0x_hex()
                stored={"binding":key,"hash":tx_hash,"raw_transaction":raw,"transaction":serial(tx)}
                self.data["transactions"][label]=stored;self.save()
            else:
                tx_hash=self.w3.eth.send_transaction(tx).to_0x_hex()
                stored={"binding":key,"hash":tx_hash,"transaction":serial(tx)}
                self.data["transactions"][label]=stored;self.save()
        if self.network=="sepolia":
            try:self.w3.eth.get_transaction(tx_hash)
            except Exception:self.w3.eth.send_raw_transaction(bytes.fromhex(stored["raw_transaction"][2:]))
        receipt=self.w3.eth.wait_for_transaction_receipt(tx_hash,timeout=120)
        stored["receipt"]=serial(dict(receipt));self.save()
        if receipt["status"]!=1: raise ValueError("Recorded transaction reverted; inspect its receipt")
        print(label+" confirmed",flush=True)
        return stored["receipt"]

def fund_roles(w3):
    writer=Writer(w3,"sepolia","sepolia-funding",create_roles=True)
    deployer=writer.signers["incb"].address
    fee=max(w3.eth.gas_price,w3.eth.get_block("latest")["baseFeePerGas"]*2+w3.eth.max_priority_fee)
    amount=max(Web3.to_wei("0.003","ether"),fee*2_000_000)
    if amount*7>Web3.to_wei("0.25","ether"):raise ValueError("Estimated role funding exceeds the configured test-ETH allowance")
    if w3.eth.get_balance(deployer)<amount*7+fee*10_000_000:raise ValueError("Deployer balance is below the estimated deployment and role funding allowance")
    receipts=[]
    for name in ROLE_NAMES:
        address=writer.signers[name].address
        # An existing completed transfer is never repeated.
        label="fund_"+name
        if writer.data["transactions"].get(label,{}).get("receipt",{}).get("status")==0:
            label+="_estimated_gas_retry"
        def build(base):
            gas=w3.eth.estimate_gas({"from":deployer,"to":address,"value":amount})
            return {**base,"to":address,"value":amount,"gas":(gas*120+99)//100,"maxFeePerGas":fee,"maxPriorityFeePerGas":min(w3.eth.max_priority_fee,fee)}
        receipts.append(writer.send(label,build,deployer,{"purpose":"fund_fresh_test_role","role":name,"address":address}))
    public={"network":"sepolia","roles":{n:a.address for n,a in writer.signers.items()},"funding_per_role_wei":amount,"receipts":receipts}
    path=ROOT/"artifacts/deployments/sepolia-roles.json";path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(public,indent=2)+"\n")
    return public
