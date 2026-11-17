"""Deploy the prototype to Anvil or explicitly configured Sepolia, with receipts."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / "src"))
from eth_account import Account
from web3 import Web3
from medcomply.config import ROOT, LOCAL, environment, rpc_url
from medcomply.chain import serial
from tools.network_writer import Writer

def deploy(network="local", new_deployment=False):
    w3 = Web3(Web3.HTTPProvider(rpc_url(network),request_kwargs={"timeout":30}))
    expected = 31337 if network == "local" else 11155111
    if w3.eth.chain_id != expected: raise ValueError("Unexpected chain; mainnet is forbidden")
    path = LOCAL / f"deployment-{network}.json"
    current_sources = {name:hashlib.sha256((ROOT/"contracts/src"/(name+".sol")).read_bytes()).hexdigest() for name in ("Registration","Compliance","Shipping")}
    if path.exists():
        existing = json.loads(path.read_text())
        if not new_deployment and existing.get("source_sha256") != current_sources:
            raise ValueError("Source differs from deployed bytecode; an explicit fresh deployment is required")
        if not new_deployment and existing["chain_id"] == expected and all(w3.eth.get_code(a) for a in existing["contracts"].values()):
            print("Existing deployment verified; no contracts redeployed.")
            return existing
        if not new_deployment: raise ValueError("Existing manifest does not match the running chain; preserve it before creating a fresh deployment")
    signer = None
    if network == "sepolia":
        value = environment().get("SEPOLIA_PRIVATE_KEY","")
        if not value: raise ValueError("Fill SEPOLIA_PRIVATE_KEY locally")
        signer = Account.from_key(value)
        sender = signer.address
    else: sender = w3.eth.accounts[0]
    writer=Writer(w3,network,"deployment-journal-"+network+("-"+str(w3.eth.block_number) if new_deployment else ""))
    def send(fn,label):
        if network=="sepolia":
            data=fn.data_in_transaction if hasattr(fn,"data_in_transaction") else fn._encode_transaction_data()
            return writer.send(label,fn.build_transaction,sender,{"label":label,"data":data})
        tx = fn.build_transaction({"from":sender,"nonce":w3.eth.get_transaction_count(sender),"chainId":expected})
        if signer:
            signed = signer.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        else: tx_hash = w3.eth.send_transaction(tx)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt["status"] != 1: raise ValueError("Deployment transaction reverted")
        return receipt
    addresses, receipts, sources = {}, [], {}
    for name in ("Registration","Compliance","Shipping"):
        artifact = json.loads((ROOT / "contracts" / "out" / f"{name}.sol" / f"{name}.json").read_text())
        factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]["object"])
        args = [] if name == "Registration" else [addresses["Registration"]]
        if name == "Shipping": args.append(addresses["Compliance"])
        receipt = send(factory.constructor(*args),name)
        addresses[name] = receipt["contractAddress"]; receipts.append(serial(dict(receipt)))
        sources[name] = hashlib.sha256((ROOT / "contracts" / "src" / f"{name}.sol").read_bytes()).hexdigest()
        print(name + ": " + receipt["contractAddress"],flush=True)
    comp_artifact = json.loads((ROOT/"contracts/out/Compliance.sol/Compliance.json").read_text())
    comp = w3.eth.contract(address=addresses["Compliance"],abi=comp_artifact["abi"])
    receipts.append(serial(dict(send(comp.functions.connectShipping(addresses["Shipping"]),"connectShipping"))))
    result = {"network":network,"chain_id":expected,"deployer":sender,"contracts":addresses,
              "deployment_block":receipts[0]["blockNumber"],"source_sha256":sources,"receipts":receipts,
              "runtime_sha256":{name:hashlib.sha256(w3.eth.get_code(address)).hexdigest() for name,address in addresses.items()},
              "quantity_unit":"ug","solidity":"0.8.28","compiler_optimizer_runs":200}
    LOCAL.mkdir(exist_ok=True)
    path.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    evidence = ROOT / "artifacts" / "deployments"
    evidence.mkdir(parents=True,exist_ok=True)
    (evidence / f"{network}-{addresses['Registration']}.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--network",choices=("local","sepolia"),default="local")
    parser.add_argument("--new-deployment",action="store_true")
    args = parser.parse_args()
    try: deploy(args.network,args.new_deployment)
    except Exception as exc:
        # RPC provider URLs may contain keys; do not print exception strings.
        print("Deployment failed (" + type(exc).__name__ + "). Check local configuration, chain ID, balance, and compiler output.",file=sys.stderr)
        sys.exit(1)
