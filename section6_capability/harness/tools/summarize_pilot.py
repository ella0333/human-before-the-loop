from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evalspec import (FULL_CAPS, MODELS, PRICES, CONTAINER_FULL_CAPS,
                      CONTAINER_PROVIDER_POLICY)


def jsonl(path: Path):
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def priced_call_costs(session_dir: Path, framework: str, model_key: str) -> list[float]:
    price = PRICES[model_key]
    usages = []
    if framework == "constrained":
        costs = []
        for row in jsonl(session_dir / "model_calls.jsonl"):
            attempt_cost = 0.0
            attempt_usage_found = False
            for attempt in row.get("request_attempts", []):
                try:
                    body = json.loads((attempt.get("response_body") or "").strip())
                    usage = body.get("usage") or {}
                except (TypeError, json.JSONDecodeError):
                    continue
                if not usage:
                    continue
                attempt_usage_found = True
                if usage.get("cost") is not None:
                    attempt_cost += float(usage["cost"] or 0)
                else:
                    attempt_cost += (
                        float(usage.get("prompt_tokens", 0) or 0) / 1_000_000 * price["in"] +
                        float(usage.get("completion_tokens", 0) or 0) / 1_000_000 * price["out"])
            if attempt_usage_found:
                costs.append(attempt_cost)
            else:
                usages.append(row.get("tokens") or {})
        return costs + [
            float(usage.get("in", 0) or 0) / 1_000_000 * price["in"] +
            float(usage.get("out", 0) or 0) / 1_000_000 * price["out"]
            for usage in usages
        ]
    else:
        costs = []
        for row in jsonl(session_dir / "openrouter_gate.jsonl"):
            if row.get("kind") != "response" or not 200 <= int(row.get("status", 0)) < 300:
                continue
            usage = row.get("usage") or {}
            if usage.get("cost") is not None:
                costs.append(float(usage.get("cost") or 0))
            else:
                costs.append(
                    float(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0) /
                    1_000_000 * price["in"] +
                    float(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0) /
                    1_000_000 * price["out"])
        if costs:
            return costs
    costs = []
    for usage in usages:
        provider_cost = usage.get("cost") or {}
        if isinstance(provider_cost, dict) and provider_cost.get("total") is not None:
            costs.append(float(provider_cost["total"] or 0))
        else:
            costs.append(
                float(usage.get("in", usage.get("input", 0)) or 0) /
                1_000_000 * price["in"] +
                float(usage.get("out", usage.get("output", 0)) or 0) /
                1_000_000 * price["out"])
    return costs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--expected-framework", nargs="+",
                        choices=("constrained", "tool_calling", "continual_harness"))
    parser.add_argument("--expected-model", choices=("glimmer", "spark"))
    args = parser.parse_args()
    rows = [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(args.run.glob("*/session_meta.json"))]
    safety_path = args.run / "safety.json"
    safety = json.loads(safety_path.read_text(encoding="utf-8")) if safety_path.is_file() else {}
    hard_spend_max = safety.get("openrouter_limit_remaining_at_launch_usd")
    by_cell = {(row.get("framework"), row.get("model_key")): row for row in rows}
    cells = []
    projected_total = 0.0
    maximum_total = 0.0
    frameworks = (tuple(args.expected_framework) if args.expected_framework
                  else ("constrained", "tool_calling", "continual_harness"))
    model_keys = ((args.expected_model,) if args.expected_model
                  else ("glimmer", "spark"))
    for framework in frameworks:
      for model_key in model_keys:
        row = by_cell.get((framework, model_key))
        projection_basis = "observed cell"
        if row is None:
            sibling_key = "spark" if model_key == "glimmer" else "glimmer"
            sibling = by_cell.get((framework, sibling_key))
            if sibling and int(sibling.get("model_calls", 0) or 0):
                sibling_calls = int(sibling["model_calls"])
                tokens = sibling.get("tokens") or {}
                per_call_in = float(tokens.get("in", 0) or 0) / sibling_calls
                per_call_out = float(tokens.get("out", 0) or 0) / sibling_calls
                price = PRICES[model_key]
                capped_cost = FULL_CAPS["model_calls"] * (
                    per_call_in / 1_000_000 * price["in"] +
                    per_call_out / 1_000_000 * price["out"])
                projected_total += capped_cost
                maximum_cost = capped_cost * 2
                maximum_total += maximum_cost
                cells.append({
                    "cell": f"{framework}__{model_key}",
                    "pilot_terminal": "missing",
                    "pilot_model_calls": 0,
                    "pilot_cost_usd": None,
                    "observed_average_cost_per_call_usd": None,
                    "projected_cost_at_full_call_cap_usd": capped_cost,
                    "empirical_high_cost_at_full_call_cap_usd": maximum_cost,
                    "projection_basis": f"proxy: {framework}__{sibling_key} tokens/call repriced for {model_key}",
                })
                continue
            cells.append({
                "cell": f"{framework}__{model_key}", "pilot_terminal": "missing",
                "pilot_model_calls": 0, "pilot_cost_usd": None,
                "observed_average_cost_per_call_usd": None,
                "projected_cost_at_full_call_cap_usd": None,
                "empirical_high_cost_at_full_call_cap_usd": None,
                "projection_basis": "unavailable",
            })
            continue
        calls = int(row.get("model_calls", 0) or 0)
        raw_costs = priced_call_costs(args.run / f"{framework}__{model_key}", framework, model_key)
        observed_cost = (sum(raw_costs) if framework == "constrained" and raw_costs
                         else float(row.get("estimated_cost_usd", 0) or 0))
        per_call = observed_cost / calls if calls else None
        capped_cost = per_call * FULL_CAPS["model_calls"] if per_call is not None else None
        worst_observed_call = max(raw_costs) if raw_costs else per_call
        maximum_cost = (worst_observed_call * FULL_CAPS["model_calls"] * 2
                        if worst_observed_call is not None else None)
        if capped_cost is not None:
            projected_total += capped_cost
        if maximum_cost is not None:
            maximum_total += maximum_cost
        cells.append({
            "cell": f"{row.get('framework')}__{row.get('model_key')}",
            "pilot_terminal": row.get("terminal"),
            "pilot_turns": row.get("turns", row.get("agent_turns", calls)),
            "pilot_meaningful_gameplay_turns": row.get(
                "meaningful_gameplay_turns", row.get("meaningful_turns")),
            "pilot_model_calls": calls,
            "pilot_cost_usd": observed_cost,
            "observed_average_cost_per_call_usd": per_call,
            "most_expensive_observed_call_usd": worst_observed_call,
            "projected_cost_at_full_call_cap_usd": capped_cost,
            "empirical_high_cost_at_full_call_cap_usd": maximum_cost,
            "projection_basis": projection_basis,
        })
    expected_rows = [row for row in rows
                     if row.get("framework") in frameworks
                     and (not args.expected_model or
                          row.get("model_key") == args.expected_model)]
    expected = len(frameworks) * len(model_keys)

    def acted(row: dict) -> bool:
        return (int(row.get("atomic_inputs", 0) or 0)
                + int(row.get("semantic_actions", 0) or 0)) > 0

    measurable_cells = sum(
        not row.get("error")
        and int(row.get("model_calls", 0) or 0) > 0
        and acted(row)
        for row in expected_rows)
    def valid(row: dict) -> bool:
        common = (row.get("terminal") == "pilot_complete"
                  and int(row.get("model_calls", 0) or 0) >= 5
                  and acted(row)
                  and not row.get("error"))
        if row.get("framework") == "tool_calling":
            return (common and int(row.get("model_calls", 0) or 0) == 5
                    and int(row.get("vision_model_calls", 0) or 0) == 5
                    and row.get("image_input_mode") == "VBA-RR emulator frames")
        if not common or row.get("framework") != "continual_harness":
            return common
        model_key = row.get("model_key")
        session_dir = args.run / f"continual_harness__{model_key}"
        expected_model = MODELS.get(model_key, "")
        actual_models = {str(model).removeprefix("openrouter/")
                         for model in row.get("actual_models", [])}
        successful_gate_calls = sum(
            item.get("kind") == "response" and 200 <= int(item.get("status", 0)) < 300
            for item in jsonl(session_dir / "openrouter_gate.jsonl"))
        return (int(row.get("model_calls", 0) or 0) == 5
                and successful_gate_calls == 5
                and row.get("scaffold") == "simplest"
                and row.get("prompt_optimization") is False
                and int(row.get("button_groups", 0) or 0) > 0
                and int(row.get("vision_model_calls", 0) or 0) > 0
                and actual_models == {expected_model}
                and row.get("provider_policy") == CONTAINER_PROVIDER_POLICY.get(model_key)
                and row.get("provider_policy_verified") is True
                and row.get("provider_evidence_verified") is True
                and len(row.get("actual_providers") or []) > 0
                and row.get("image_input_mode") == "native Continual Harness screenshots")

    pilot_valid = len(expected_rows) == expected and all(valid(row) for row in expected_rows)
    completed_cells = sum(row.get("terminal") == "pilot_complete" for row in expected_rows)
    model_totals = {}
    for model_key in model_keys:
        matching = [cell for cell in cells if cell["cell"].endswith(f"__{model_key}")]
        projected = [cell["projected_cost_at_full_call_cap_usd"] for cell in matching]
        maximum = [cell["empirical_high_cost_at_full_call_cap_usd"] for cell in matching]
        model_totals[model_key] = {
            "full_cells": len(matching),
            "projected_cost_at_full_call_caps_usd": (
                sum(projected) if all(value is not None for value in projected) else None),
            "empirical_high_cost_at_full_call_caps_usd": (
                sum(maximum) if all(value is not None for value in maximum) else None),
        }
    report = {
        "purpose": ("five-paid-call scoped validity check and capped run cost estimate"
                    if args.expected_framework or args.expected_model else
                    "five-paid-call four-cell validity check and capped full-eval cost estimate"),
        "pilot_sessions_found": len(rows),
        "pilot_valid": pilot_valid,
        "pilot_measurable_cells": measurable_cells,
        "pilot_model_call_target_cells_completed": completed_cells,
        "pilot_model_call_target_cells_expected": expected,
        "pilot_total_model_calls": sum(int(row.get("model_calls", 0) or 0) for row in rows),
        "pilot_observed_cost_usd": sum(
            float(cell.get("pilot_cost_usd", 0) or 0) for cell in cells),
        "full_eval": f"one tutorial-completion attempt in each of {expected} requested framework/model cells",
        "full_per_cell_call_cap": FULL_CAPS["model_calls"],
        "full_total_call_cap": expected * FULL_CAPS["model_calls"],
        "projected_full_cost_at_call_caps_usd": projected_total if all(
            cell["projected_cost_at_full_call_cap_usd"] is not None for cell in cells) else None,
        "empirical_high_full_model_cost_usd": maximum_total if all(
            cell["empirical_high_cost_at_full_call_cap_usd"] is not None for cell in cells) else None,
        "empirical_high_estimate_rule": (
            f"For each cell: 2 x the highest observed per-call cost x {FULL_CAPS['model_calls']}-call "
            f"full cap; then sum cells. This is not a hard maximum because context can grow during "
            f"a {FULL_CAPS['model_calls']}-call run."
        ),
        "hard_maximum_model_spend_before_credit_pause_usd": hard_spend_max,
        "hard_maximum_basis": (
            "OpenRouter per-key limit remaining at launch; applies across the requested cells unless changed while running."
        ),
        "container_emergency_ceiling_hours": (
            (1 if args.expected_model else 2) *
            CONTAINER_FULL_CAPS["wallclock_seconds"] / 3600),
        "container_cost_included": False,
        "projection_uses_proxy_cells": any(
            str(cell["projection_basis"]).startswith("proxy:") for cell in cells),
        "projection_method": (
            "Observed cells use pilot cost / calls x the full call cap. A missing cell uses the other model's "
            "tokens/call in the same framework, repriced at the missing model's rates. This is a cap-based "
            "planning estimate. The empirical high estimate doubles the most expensive observed call before "
            "applying the cap, but is not a hard maximum because context can grow. The hard model-spend maximum "
            "is the OpenRouter per-key remaining limit recorded at launch. container CPU cost is separate."
        ),
        "per_model_full_totals": model_totals,
        "cells": cells,
    }
    target = args.run / "pilot_summary.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n" + "=" * 72)
    print(("SCOPED FULL-RUN COST ESTIMATE" if args.expected_framework or args.expected_model
           else "FULL 2x2 EVAL COST ESTIMATE (TOTAL ACROSS ALL 4 CELLS)"))
    print("=" * 72)
    print(f"PILOT {'VALID' if pilot_valid else 'INVALID'}: "
            f"{measurable_cells}/{expected} measurable cells; "
            f"model-call target {completed_cells}/{expected}")
    if report["projected_full_cost_at_call_caps_usd"] is not None:
        print("-")
        print("MODEL TOTALS (requested cells; observed-rate - empirical high)")
        for model_key, label in (("glimmer", "Muse Glimmer 30B"), ("spark", "Muse Spark 1.2")):
            if model_key not in model_totals:
                continue
            subtotal = model_totals[model_key]
            print(f"  {label:<20} ${subtotal['projected_cost_at_full_call_caps_usd']:>7.2f}"
                  f" - ${subtotal['empirical_high_cost_at_full_call_caps_usd']:>7.2f}")
        print("-")
        print(f"EMPIRICAL TOTAL COST RANGE: ${report['projected_full_cost_at_call_caps_usd']:.2f}"
              f" - ${report['empirical_high_full_model_cost_usd']:.2f} TOTAL")
        print(f"LOW OBSERVED-RATE TOTAL  : ${report['projected_full_cost_at_call_caps_usd']:.2f}")
        print(f"EMPIRICAL HIGH TOTAL     : ${report['empirical_high_full_model_cost_usd']:.2f}")
        if report["hard_maximum_model_spend_before_credit_pause_usd"] is not None:
            print(f"HARD MAX MODEL SPEND BEFORE CREDIT PAUSE: "
                  f"${report['hard_maximum_model_spend_before_credit_pause_usd']:.2f}")
        print("CONTAINER CPU                : separate; call-cap-first with a high emergency ceiling")
        print(f"PILOT COST                : ${report['pilot_observed_cost_usd']:.2f}")
    else:
        print("INCOMPLETE: insufficient cell evidence for the requested projection")
    print("=" * 72 + "\n")
    print(json.dumps(report, indent=2))
    return 0 if pilot_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
