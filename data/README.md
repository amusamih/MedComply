# Synthetic compliance datasets

All records in this directory are generated for research. They do not contain real shipments, patient records or government authorizations.

| File | Contents |
| --- | --- |
| `component_tasks.jsonl` | 1,500 test cases, with 300 for each of five compliance tasks |
| `component_development.jsonl` | 100 development cases, with 20 per task, excluded from test scores |
| `workflow_scenarios.json` | 60 shipment scenarios covering national import, national export and global assessment |

The five component tasks are quota limits, quota reconciliation, database queries, cross-source consistency and permit validity. Each JSONL record separates `input` from its `expected` result. Workflow records similarly separate the supplied `input` from `expected_decision` and `scenario_type`. Scoring labels and scenario-type metadata are not model inputs.

Workflow scenarios apply the supplied synthetic requirements to USA-to-UK morphine shipments in quota year 2025. Their expected decisions follow those requirements. They include quota boundaries, inconsistent records, missing evidence, expired authorizations and embedded instructions. Each scenario has two recorded assessments in `evaluation/workflow_results.csv`.

Run `python -m evaluation.verify` from the repository root to verify counts, labels, result coverage and the published file hashes.
