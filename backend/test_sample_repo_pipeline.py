"""
DocProof — sample_repo pipeline smoke test.
"""

import json
import sys
from collections import Counter
from pathlib import Path

REPO_PATH = "../sample_repo"
BACKEND_PATH = "."

try:
    from app.orchestration.pipeline import run
    from app.verification.trust_score import calculate_trust_score
except ImportError as e:
    print(f"[ERROR] Could not import pipeline modules: {e}")
    sys.exit(1)


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    line = f"[{status}] {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return condition


def main() -> None:
    print(f"Running full pipeline on: {REPO_PATH}\n")
    result = run(repo_path=REPO_PATH, backend_path=BACKEND_PATH)

    all_ok = True

    reverified_ids = [c.id for c in result.reverification.contracts]
    id_counts = Counter(reverified_ids)
    duplicates = {cid: n for cid, n in id_counts.items() if n > 1}

    ok = check(
        "No duplicate IDs after reverification",
        len(duplicates) == 0,
        f"initial={len(result.contracts)} reverified={len(result.reverification.contracts)}"
        + (f" duplicates={duplicates}" if duplicates else ""),
    )
    all_ok &= ok

    ok = check(
        "Reverification count matches initial count",
        len(result.reverification.contracts) == len(result.contracts),
        f"expected {len(result.contracts)}, got {len(result.reverification.contracts)}",
    )
    all_ok &= ok

    fixed_contracts = [c for c in result.reverification.contracts if c.reverified]
    stale_evidence = [
        c.id for c in fixed_contracts
        if any(word in c.evidence.lower() for word in ["inconsistent", "not found", "mismatch", "missing"])
    ]
    ok = check(
        "Evidence refreshed on fixed contracts (no stale mismatch wording)",
        len(stale_evidence) == 0,
        f"{len(fixed_contracts)} fixed contracts checked"
        + (f", stale: {stale_evidence}" if stale_evidence else ""),
    )
    all_ok &= ok

    trust_before = result.trust_score
    trust_after = getattr(result, "trust_score_after", None)
    if trust_after is None:
        trust_after = calculate_trust_score(result.reverification.contracts)

    ok = check(
        "Trust score improves after fix + re-verification",
        trust_after > trust_before,
        f"before={trust_before} after={trust_after}",
    )
    all_ok &= ok

    initial_claims = " | ".join(c.claim.lower() for c in result.contracts)
    expected_signals = ["node", "python", "secret_key"]
    missing_signals = [sig for sig in expected_signals if sig not in initial_claims]
    ok = check(
        "Known sample_repo mismatches detected (Node / Python / SECRET_KEY)",
        len(missing_signals) == 0,
        f"missing: {missing_signals}" if missing_signals else "all 3 detected",
    )
    all_ok &= ok

    print("\n" + "=" * 60)
    print(f"RESULT: {'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED'}")
    print("=" * 60)

    out_path = Path("pipeline_test_output.json")
    out_path.write_text(json.dumps(result.model_dump(), indent=2))
    print(f"\nFull output saved to: {out_path.resolve()}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()