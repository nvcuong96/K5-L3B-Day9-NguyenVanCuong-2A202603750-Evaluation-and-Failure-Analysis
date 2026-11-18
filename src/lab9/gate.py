"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []

    for mid, val in (pr.get("metrics") or {}).items():
        if isinstance(val, (int, float)) and not math.isfinite(float(val)):
            problems.append(f"metric {mid} is NaN or infinity")

    pr_manifest = pr.get("manifest") or {}
    pr_rows = pr.get("rows") or []
    expected_rows = int(pr_manifest.get("items") or 0) * len(pr_manifest.get("seeds") or [])
    if len(pr_rows) != expected_rows:
        problems.append(
            f"row count {len(pr_rows)} differs from manifest items x seeds {expected_rows}"
        )

    def item_seed_keys(report: dict) -> set[tuple[str, int]]:
        return {
            (str(row.get("item_id")), int(row["seed"]))
            for row in report.get("rows") or []
            if row.get("item_id") is not None and row.get("seed") is not None
        }

    if item_seed_keys(pr) != item_seed_keys(base):
        problems.append("PR item/seed set differs from baseline: cherry-picked rows")

    if pr.get("evaluator_failures"):
        problems.append("PR contains evaluator failures")

    graded_ids = {
        m["id"] for m in spec.get("metrics", [])
        if m["id"] in GRADERS
    }
    if any(row.get(mid) is None for row in pr_rows for mid in graded_ids):
        problems.append("a graded metric has None on a PR row")

    pr_slices = {
        str(row.get("item_id")): tuple(row.get("slices") or [])
        for row in pr_rows
    }
    base_slices = {
        str(row.get("item_id")): tuple(row.get("slices") or [])
        for row in (base.get("rows") or [])
    }
    if pr_slices != base_slices:
        problems.append("item slice tags differ from baseline: relabelled slice")

    if base_spec is not None:
        current_metrics = {m["id"]: m for m in spec.get("metrics", [])}
        base_metrics = {m["id"]: m for m in base_spec.get("metrics", [])}
        if set(current_metrics) != set(base_metrics):
            problems.append("metric set differs from baseline spec")
        for mid, old in base_metrics.items():
            new = current_metrics.get(mid)
            if new is None:
                continue
            old_th = old.get("threshold") or {}
            new_th = new.get("threshold") or {}
            if "max" in old_th and "max" in new_th and float(new_th["max"]) > float(old_th["max"]):
                problems.append(f"{mid}: safety max threshold was loosened")
            if "min" in old_th and "min" in new_th and float(new_th["min"]) < float(old_th["min"]):
                problems.append(f"{mid}: capability min threshold was loosened")
            if old.get("blocking") and not new.get("blocking"):
                problems.append(f"{mid}: blocking metric became non-blocking")

        old_slices = {s["name"]: s for s in base_spec.get("slices", [])}
        new_slices = {s["name"]: s for s in spec.get("slices", [])}
        if set(new_slices) != set(old_slices):
            problems.append("slice set differs from baseline spec")
        for name, old in old_slices.items():
            new = new_slices.get(name)
            if new is None:
                continue
            if old.get("blocking") and not new.get("blocking"):
                problems.append(f"slice {name}: blocking became non-blocking")
            if old.get("no_significant_drop") and not new.get("no_significant_drop"):
                problems.append(f"slice {name}: no_significant_drop was removed")
            if "min" in old and "min" in new and float(new["min"]) < float(old["min"]):
                problems.append(f"slice {name}: min threshold was loosened")

    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})

    for s in spec.get("slices", []):
        name = s["name"]
        metric = s["metric"]
        pr_item = per_item(pr, metric, name)
        pr_value = sum(pr_item.values()) / len(pr_item) if pr_item else 0.0
        verdict = "ok"
        note = ""

        if "min" in s and pr_value < float(s["min"]):
            verdict = "fail"

        if s.get("no_significant_drop"):
            pr_item_values = per_item(pr, metric, name)
            base_item_values = per_item(base, metric, name)
            keys = sorted(set(pr_item_values) & set(base_item_values))
            if keys:
                _, lo, hi = stats.paired_bootstrap(
                    [pr_item_values[k] for k in keys],
                    [base_item_values[k] for k in keys],
                    n_resamples=bs.get("n_resamples", 1000),
                    rng=bs.get("rng", 2027),
                )
                note = f"CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdict = "fail"

        slice_id = f"slice:{name}/{metric}"
        if verdict != "ok" and s.get("blocking"):
            blocked.append(slice_id)
        label = "BLOCK" if verdict != "ok" and s.get("blocking") else ("warn" if verdict != "ok" else "ok")
        lines.append(f"  {slice_id:<32} {pr_value:.4f}  {note:<28} {label}")

    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
