"""Create explicit synthetic local actors, quota declarations, and three consignments."""
from __future__ import annotations
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / "src"))
from medcomply.config import LOCAL, ROOT
from medcomply.chain import Chain, country, substance, serial
from medcomply.storage import Documents
from medcomply.db import initialize, session, Document, audit, digest
from tools.network_writer import Writer

def seed(network="local"):
    chain = Chain(network)
    result_path = LOCAL / ("demo.json" if network=="local" else "demo-sepolia.json")
    if result_path.exists():
        existing = json.loads(result_path.read_text())
        same=existing.get("contracts")==chain.deployment["contracts"] or existing["receipts"][0]["to"].lower()==chain.reg.address.lower()
        if same and chain.ship.functions.nextShipmentId().call() > 1:
            print("Existing demo retained."); return existing
        old_address=existing["receipts"][0]["to"]
        (ROOT/"artifacts/deployments"/(old_address+"-demo.json")).write_text(json.dumps(existing,indent=2)+"\n")
    initialize()
    storage = Documents()
    writer=Writer(chain.w3,network,"seed-"+network+"-"+chain.reg.address)
    accounts = chain.w3.eth.accounts if network=="local" else []
    if network=="sepolia": roles={name:a.address for name,a in writer.signers.items()}
    else: roles = {"incb":accounts[0],"nca_usa":accounts[1],"nca_gbr":accounts[2],"exporter":accounts[3],
                  "importer":accounts[4],"logistics":accounts[5],"customs_usa":accounts[6],"customs_gbr":accounts[7]}
    if "seed_timestamp" not in writer.data:
        writer.data["seed_timestamp"]=chain.w3.eth.get_block("latest")["timestamp"]
        writer.data["first_shipment_id"]=chain.ship.functions.nextShipmentId().call();writer.save()
    receipts = []
    def send(contract,fn,args,actor):
        label="seed_"+str(len(receipts))+"_"+fn
        function=getattr(chain.contracts[contract].functions,fn)(*args)
        receipt=writer.send(label,function.build_transaction,actor,{"contract":contract,"function":fn,"args":serial(args),"actor":actor})
        receipts.append(receipt)
        return receipt
    def store(value,owner,filename):
        content = (json.dumps(value,sort_keys=True,indent=2)+"\n").encode()
        record = storage.add(content,filename)
        with session() as db:
            if not db.get(Document,record["cid"]):
                db.add(Document(cid=record["cid"],sha256=record["sha256"],filename=filename,media_type="application/json",owner=owner))
            audit(db,owner,"seed_document",{"cid":record["cid"],"sha256":record["sha256"]}); db.commit()
        return record["cid"]
    review = bytes.fromhex(digest({"purpose":"explicit_synthetic_demo_seed","year":2025}))
    send("Registration","registerNCA",[roles["nca_usa"],country("USA")],roles["incb"])
    send("Registration","registerNCA",[roles["nca_gbr"],country("GBR")],roles["incb"])
    timestamp = writer.data["seed_timestamp"]
    for name,role,iso,nca in [("exporter",2,"USA","nca_usa"),("importer",4,"GBR","nca_gbr"),
                                ("logistics",3,"USA","nca_usa"),("customs_usa",1,"USA","nca_usa"),("customs_gbr",1,"GBR","nca_gbr")]:
        doc = {"document_type":"license","synthetic":True,"account":roles[name],"country":iso,
               "role":name.split('_')[0],"substances":["morphine"],"valid_from":timestamp,"valid_until":timestamp+365*86400}
        cid = store(doc,roles[name],name+"-license.json")
        send("Registration","requestReg",[country(iso),role,cid],roles[name])
        send("Registration","approveReg",[roles[name],timestamp+365*86400,review],roles[nca])
    declaration = {"document_type":"quota_declaration","synthetic":True,"policy_version":"demo-2025-v1",
                   "year":2025,"unit":"ug","quotas":[{"country":iso,"substance":"morphine","mode":mode,
                   "nationalCap":220_000_000_000,"globalCap":200_000_000_000} for iso,mode in [("USA","export"),("GBR","import")]]}
    quota_cid = store(declaration,roles["incb"],"quota-2025.json")
    send("Compliance","setIncbQuotas",[2025,quota_cid,review],roles["incb"])
    for iso,mode,nca in [("USA",True,"nca_usa"),("GBR",False,"nca_gbr")]:
        send("Shipping","setQuota",[country(iso),substance("morphine"),2025,mode,False,200_000_000_000,review],roles["incb"])
        send("Shipping","setQuota",[country(iso),substance("morphine"),2025,mode,True,220_000_000_000,review],roles[nca])
        send("Shipping","correctQuotaUsage",[country(iso),substance("morphine"),2025,mode,190_000_000_000,review],roles["incb"])
    shipments = []
    for label,quantity,document_quantity in [("Within quota",5_000_000_000,5_000_000_000),
                                             ("Exceeds global quota",15_000_000_000,15_000_000_000),
                                             ("Conflicting shipment documents",5_000_000_000,4_000_000_000)]:
        shipment_id = writer.data["first_shipment_id"]+len(shipments)
        doc = {"document_type":"shipment","synthetic":True,"shipment_id":shipment_id,"exporter":roles["exporter"],
               "importer":roles["importer"],"origin":"USA","destination":"GBR","substance":"morphine",
               "quantity":document_quantity,"unit":"ug","year":2025,"policy_version":"demo-2025-v1"}
        cid = store(doc,roles["exporter"],f"shipment-{shipment_id}.json")
        send("Shipping","createShipment",[roles["importer"],roles["logistics"],substance("morphine"),quantity,2025,cid],roles["exporter"])
        shipments.append({"id":shipment_id,"label":label,"quantity":quantity,"documentCID":cid})
    result = {"synthetic":True,"network":network,"chain_id":chain.w3.eth.chain_id,"contracts":chain.deployment["contracts"],"roles":roles,"shipments":shipments,
              "quota_document_cid":quota_cid,"receipts":receipts,"policy_version":"demo-2025-v1"}
    result_path.write_text(json.dumps(result,indent=2)+"\n")
    path = ROOT/f"artifacts/deployments/{network}-demo.json"
    path.write_text(json.dumps(result,indent=2)+"\n")
    print("Synthetic demo seeded: 8 identities, 3 consignments, explicit microgram units.")
    return result

if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument("--network",choices=("local","sepolia"),default="local")
    try: seed(parser.parse_args().network)
    except Exception as exc: print("Seed stopped: "+type(exc).__name__);raise SystemExit(1)
