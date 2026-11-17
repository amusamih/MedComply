# Evaluation

The component evaluation and complete workflow evaluation address different questions. Component results compare five models on the same five synthetic tasks, with 300 test cases per task. Every required output field must match the scoring rule; database queries must return the requested information under filter-discriminating records. Failed or unusable responses remain in the denominator.

Lifecycle and permit-renewal checks use separate application and Solidity tests. Scripted assessment responses in those checks exercise application controls and do not count as model-accuracy observations.

The workflow evaluation uses Gemini 3.8 Flash to assess 60 synthetic scenarios twice. The model recommendation is recorded separately from the final recommendation after application checks. Infrastructure reads use saved evidence, while the workflow's record checks, quota calculations, source validation and response handling remain active.

| Workflow measure | Recorded result |
| --- | ---: |
| Final recommendation matches expected decision | 108/120 (90.00%) |
| 95% confidence interval, resampling scenarios with their paired repetitions | 82.50â€“96.67% |
| Unsupported final approvals in the tested cases | 0/120 |
| Final responses passing evidence checks | 119/120 |
| Scenarios correct in both repetitions | 53/60 |
| Same final decision in both repetitions | 58/60 |
| Median processing time over saved evidence | 10.422 s |
| Mean estimated inference cost at study rates | USD 0.00834 |

Eleven of the twelve final decision errors refer confirmed violations for review instead of rejection. The remaining error refers an eligible case for review. One timed-out request also returns review, which matches its expected decision but supplies no model-generated assessment. The CSV represents that unavailable model recommendation as an empty field.

Processing times exclude live blockchain/document/database retrieval, human review and transaction confirmation. Inference estimates use recorded token usage and study rates, including a conservative allowance for the timed-out request. The reported interval uses 5,000 scenario-level bootstrap resamples so each pair of repetitions stays together. These synthetic, internally specified cases characterize this configuration and do not establish operational regulatory validity.

`component_results.csv` contains task-level counts. `workflow_results.csv` contains the 120 assessment outcomes and their processing/inference measurements. Synthetic inputs and expected decisions are in `../data/`. Source and data hashes are listed in `source-manifest.json`.

```powershell
python -m evaluation.verify
```

This command validates the published data/results and recomputes agreement, repetitions, costs and the bootstrap interval without network access or model calls.
