"""Check the published synthetic data and recompute workflow summary statistics."""
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import random
import statistics

ROOT = Path(__file__).resolve().parents[1]

def main():
    manifest = json.loads((ROOT / "evaluation/source-manifest.json").read_text())
    for name, expected in manifest["sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    component = [json.loads(line) for line in (ROOT / "data/component_tasks.jsonl").read_text().splitlines()]
    assert len(component) == 1500
    assert set(Counter(c["task"] for c in component).values()) == {300}
    scenarios = json.loads((ROOT / "data/workflow_scenarios.json").read_text())
    assert len(scenarios) == 60 and len({s["id"] for s in scenarios}) == 60
    expected = {s["id"]: s for s in scenarios}
    with (ROOT / "evaluation/workflow_results.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 120
    groups = defaultdict(list)
    for row in rows:
        scenario = expected[row["case_id"]]
        assert row["expected"] == scenario["expected_decision"]
        assert row["stage"] == scenario["stage"]
        assert int(row["final_correct"]) == (row["expected"] == row["final_recommendation"])
        assert int(row["model_correct"]) == (row["expected"] == row["model_recommendation"])
        assert int(row["unsupported_final_approval"]) == (row["final_recommendation"] == "approve" and row["expected"] != "approve")
        groups[row["case_id"]].append(row)
    assert len(groups) == 60 and all({r["repeat"] for r in g} == {"1", "2"} for g in groups.values())
    assert sum(int(r["final_correct"]) for r in rows) == 108
    assert sum(int(r["unsupported_final_approval"]) for r in rows) == 0
    assert sum(int(r["evidence_checks_pass"]) for r in rows) == 119
    assert sum(all(int(r["final_correct"]) for r in g) for g in groups.values()) == 53
    assert sum(len({r["final_recommendation"] for r in g}) == 1 for g in groups.values()) == 58
    means = [statistics.mean(int(r["final_correct"]) for r in g) for g in groups.values()]
    rng = random.Random(2026102402)
    samples = sorted(statistics.mean(rng.choices(means, k=len(means))) for _ in range(5000))
    interval = [round(100 * samples[index], 2) for index in (124, 4874)]
    assert interval == [82.50, 96.67]
    median = statistics.median(float(r["processing_seconds"]) for r in rows)
    average = statistics.mean(float(r["estimated_inference_usd"]) for r in rows)
    assert round(median, 3) == 10.422 and round(average, 5) == 0.00834
    print(json.dumps({"status": "passed", "component_cases": len(component), "workflow_scenarios": len(groups),
                      "assessments": len(rows), "correct": 108, "confidence_interval_95_percent": interval,
                      "median_processing_seconds": median, "mean_estimated_inference_usd": average}))

if __name__ == "__main__":
    main()
