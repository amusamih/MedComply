from __future__ import annotations
import hashlib
import io
import json
import re
import httpx
from pypdf import PdfReader

MAX_DOCUMENT_BYTES = 2_000_000
CID_PATTERN = re.compile(r"(?:b[a-z2-7]{20,120}|Qm[1-9A-HJ-NP-Za-km-z]{44})\Z")

class Documents:
    def __init__(self, api="http://127.0.0.1:5001"):
        self.api = api

    def add(self, content: bytes, filename="document.json", media_type="application/json"):
        if not 0 < len(content) <= MAX_DOCUMENT_BYTES:
            raise ValueError("Document must contain 1 to 2,000,000 bytes")
        response = httpx.post(self.api + "/api/v0/add", params={"pin":"true", "cid-version":"1", "raw-leaves":"true"},
                              files={"file":(filename, content, media_type)}, timeout=30)
        response.raise_for_status()
        cid = response.json()["Hash"]
        if not CID_PATTERN.fullmatch(cid): raise ValueError("Unexpected storage identifier")
        return {"cid":cid, "sha256":hashlib.sha256(content).hexdigest(), "bytes":len(content)}

    def read(self, cid, expected_hash=None):
        if not CID_PATTERN.fullmatch(cid): raise ValueError("Invalid document CID")
        with httpx.stream("POST", self.api + "/api/v0/cat", params={"arg":cid}, timeout=30) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > MAX_DOCUMENT_BYTES: raise ValueError("Document exceeds retrieval limit")
        content = bytes(content)
        if expected_hash and hashlib.sha256(content).hexdigest() != expected_hash:
            raise ValueError("Document SHA-256 differs from the recorded value")
        return content

    def text(self, cid, expected_hash=None):
        content = self.read(cid, expected_hash)
        if content.startswith(b"%PDF"):
            return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
        return content.decode("utf-8")

    def json(self, cid, expected_hash=None): return json.loads(self.text(cid, expected_hash))
