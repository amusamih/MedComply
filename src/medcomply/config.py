from __future__ import annotations
import json
import os
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / ".local"

def environment():
    values = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip().strip("\"").strip("'")
    values.update(os.environ)
    return values

def database_url():
    explicit = environment().get("DATABASE_URL")
    if explicit:
        return explicit
    password = (LOCAL / "database-password.txt").read_text().strip()
    return "postgresql+psycopg://medcomply:" + quote(password, safe="") + "@127.0.0.1:55432/medcomply"

def manifest(network="local"):
    if network not in ("local", "sepolia"):
        raise ValueError("Unsupported network")
    return json.loads((LOCAL / f"deployment-{network}.json").read_text())

def rpc_url(network="local"):
    if network == "local":
        return "http://127.0.0.1:8545"
    value = environment().get("SEPOLIA_RPC_URL", "")
    if not value.startswith("https://"):
        raise ValueError("An HTTPS Sepolia RPC URL is required in .env")
    return value
