# Compliance Assurance for Cross-Border Shipping of Controlled Medications Using Blockchain and Large Language Models

**MedComply** is a research prototype that combines Ethereum smart contracts with large language model (LLM)-based assessment of shipment records, permits, supporting documents and quota information. It provides evidence-linked recommendations for human regulatory review and tracks quantity commitments throughout the shipment lifecycle.

The application uses prescribed national and global assessment workflows. Software checks enforce record consistency and quota calculations, while the LLM interprets supplied document and policy text. Authorized participants sign regulatory transactions separately from the assessment.

## Capabilities

- Register authorities and operational participants with country-specific roles and licensing checks.
- Issue shipment-bound import and export permits through separate review and approval steps.
- Reserve export and import allowances atomically at shipping approval, then record consumption at pickup and import clearance.
- Replace an invalid import permit during transit while preserving the shipment's existing quota commitments.
- Compare blockchain records, IPFS documents and cached quota information, with explicit review of discrepancies.
- Prepare permit drafts and evidence-linked assessments through a browser interface.

## Architecture

| Component | Responsibility |
| --- | --- |
| Solidity smart contracts | Participant registration, permits, shipment states and quota accounting |
| FastAPI and browser interface | Assessment requests, evidence review, permit drafts and reviewed transactions |
| LangGraph and LangChain | Workflow orchestration and read-only evidence tools |
| LLM provider interface | Structured interpretation of supplied document and policy text |
| PostgreSQL | Application records, review decisions and quota cache |
| IPFS/Kubo | Supporting documents and published permit records |

See [the architecture guide](docs/architecture.md) for component boundaries and the shipment lifecycle.

## Quick start on Windows

Prerequisites are Python 3.12, Docker Desktop with Docker Compose, and [Foundry](https://getfoundry.sh/) with `forge` and `anvil` on `PATH`.

```powershell
git clone https://github.com/amusamih/MedComply.git
cd MedComply
Copy-Item .env.example .env
# Enter your provider API key in .env locally.
./tools/start_local.ps1 -Install
```

Open **http://127.0.0.1:8765**. The startup script creates a Python environment, starts PostgreSQL, IPFS and Anvil, deploys the smart contracts, and seeds a synthetic demonstration. Services bind to loopback. The local identity selector supplies the demonstration roles without a browser wallet extension.

Gemini is the default provider. Other configured providers are selectable, but the complete workflow evaluation uses Gemini 3.8 Flash. Live assessments require provider credentials and incur inference charges. The shipped inference configuration applies request, timeout and spending limits; see [configuration and development](docs/development.md).

For subsequent starts, run `./tools/start_local.ps1`. If the ephemeral Anvil chain has been restarted, use `./tools/start_local.ps1 -NewFixture` to create a fresh deployment. PostgreSQL and IPFS persist in Docker volumes; retain the corresponding `.local/database-password.txt` file.

## Demonstration workflow

The synthetic example concerns morphine shipments from the United States to the United Kingdom in quota year 2025. It includes an eligible consignment, a quantity exceeding the global allowance, and conflicting shipment documents.

1. The importing authority requests an assessment and reviews the permit draft.
2. The importer submits the published permit reference, and the authority separately approves issuance.
3. The exporting side follows the corresponding process using the linked import authorization.
4. Export customs records clearance, followed by global assessment and human shipping approval.
5. Shipping approval reserves both allowances. Pickup consumes the export reservation; import clearance consumes the import reservation; the importer confirms receipt.

The INCB-facing shipping-approval role is a proposed governance arrangement used by the prototype. The supplied policy and records are synthetic, and the application does not issue real government authorizations. Quantities use integer micrograms, with 1 gram equal to 1,000,000 micrograms.

## Tests and evaluation

Run the offline Python tests and Solidity tests from the repository root:

```powershell
.venv/Scripts/python.exe -m pytest -q
forge test --root contracts --fuzz-runs 1024
.venv/Scripts/python.exe -m evaluation.verify
```

Python tests cover quantity handling, evidence checks, response validation, quota reconciliation, review boundaries and provider request safeguards. Solidity tests cover authorization, permit linkage, lifecycle transitions, reservation accounting and permit replacement. Optional service integration tests are described in [the development guide](docs/development.md).

The repository includes the [synthetic datasets](data/README.md) and [evaluation results](evaluation/README.md). Component tests compare five LLMs on five tasks. The complete Gemini workflow assessment covers 60 scenarios evaluated twice, with 108 of 120 final recommendations matching the expected decision and no unsupported final approvals in those tested cases. These are synthetic research results, not operational certification.

## Sepolia

The application supports local Anvil (chain ID 31337) and Ethereum Sepolia (11155111). For a separate testnet deployment, configure a Sepolia RPC URL and a testnet-only deployment key locally. Public-network transactions use wallet signatures and explicit participant review. The [reference Sepolia deployment](deployments/sepolia.json) records the smart contracts used for lifecycle checks. Its [Shipping transaction history](https://eth-sepolia.blockscout.com/address/0xe47eA101907bAba53c02a2d451AaB548A48FFB2C) is publicly inspectable. See [the deployment instructions](docs/development.md#sepolia) before creating your own deployment.

## Repository structure

```text
contracts/    Solidity smart contracts and Foundry tests
src/medcomply/ Application backend, workflows and browser assets
policies/     Synthetic policy and workflow configuration
deployments/  Public testnet deployment metadata
tools/        Local startup, deployment and data-generation utilities
tests/        Offline checks and optional service integration tests
data/         Synthetic compliance tasks and shipment scenarios
evaluation/   Study results and offline verification
docs/         Architecture, configuration and development guides
```

## License

This project is available under the [MIT license](LICENSE).
