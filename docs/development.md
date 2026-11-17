# Configuration and development

## Local services

The supported local startup is `./tools/start_local.ps1 -Install` on Windows. Docker Compose starts PostgreSQL on `127.0.0.1:55432`, the Kubo API on `127.0.0.1:5001`, and its gateway on `127.0.0.1:8081`. Anvil uses port 8545 and the application uses port 8765. The script uses the pinned Python dependencies in `requirements.lock.txt` and installs the project in editable mode.

The demonstration starts Anvil at a synthetic 2025 timestamp. Licenses, permits and quota year therefore belong to the demonstration timeline. Restarting Anvil creates a different ephemeral chain; request a new fixture rather than treating its history as a continuation of the old chain.

The normal application startup creates runtime state under `.local/` and generated records under `artifacts/`. These directories are excluded from version control. Keep the database password with its Docker volume and preserve inference spending records when continuing a run.

## Provider configuration

Copy `.env.example` to `.env` and set the key for the selected provider. The configured names are `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY` and `DEEPSEEK_API_KEY`. Provider model identifiers and request handling are in `tools/provider_preflight.py`; the default provider is set in `policies/application-release-v1.json`.

The evaluated workflow permits at most eight model dispatches, three draft attempts and 180 seconds per assessment. It applies a USD 0.12 per-assessment allowance, a USD 1.50 workflow-group ceiling and a USD 20 shared inference ceiling. These are prototype spending safeguards, not advertised service limits or provider balances. Estimated costs use the configured study rates; check provider availability and current pricing before a new live run. No key is required for offline tests.

## Offline checks

```powershell
.venv/Scripts/python.exe -m pytest -q
forge test --root contracts --fuzz-runs 1024
.venv/Scripts/python.exe -m evaluation.verify
```

The default Python suite skips tests that require services. After creating the local fixture, explicitly enable those tests with:

```powershell
$env:MEDCOMPLY_INTEGRATION = '1'
.venv/Scripts/python.exe -m pytest -q -m integration
Remove-Item Env:MEDCOMPLY_INTEGRATION
```

Integration tests use the running local services and synthetic records. They check authentication, replay handling and quota-cache integrity without paid inference. Use a development fixture because they write temporary test state.

## Sepolia

Configure `SEPOLIA_RPC_URL` and a testnet-only `SEPOLIA_PRIVATE_KEY` in `.env`. Start PostgreSQL/IPFS and build the Solidity artifacts before deploying:

```powershell
forge build --root contracts
.venv/Scripts/python.exe -m tools.deploy --network sepolia
.venv/Scripts/python.exe -m tools.seed_demo --network sepolia
.venv/Scripts/python.exe -m tools.refresh_fixture_cache --network sepolia
```

Deployment and fixture setup send testnet transactions. The deployer needs Sepolia ETH; the fixture tooling creates separate role wallets and records transaction receipts locally. Use only testnet wallets, never Anvil's publicly known development keys on Sepolia. Browser users authenticate with a wallet signature and sign their own reviewed transactions. The application acknowledges Sepolia receipts after twelve confirmations.

## Data utilities

`python -m tools.benchmark_data` regenerates the seeded component datasets into an ignored output directory. `evaluation/scoring.py` checks generated SQL against the requested result and additional records that distinguish each filter. `python -m evaluation.verify` checks the published dataset/result relationships and recomputes the reported workflow agreement interval without provider calls.
