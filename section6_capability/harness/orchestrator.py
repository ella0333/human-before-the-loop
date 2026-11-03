from __future__ import annotations

import argparse
from pathlib import Path

import pilot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Pokemon Red tutorial evaluation."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--pilot",
        action="store_true",
        help="six-cell smoke/cost pilot, exactly one short session per cell",
    )
    mode.add_argument(
        "--full",
        action="store_true",
        help="one tutorial-completion attempt in each of the six cells",
    )
    lane = parser.add_mutually_exclusive_group()
    lane.add_argument(
        "--local-only",
        action="store_true",
        help="run only the local cells: constrained, then tool-calling, Glimmer then Spark",
    )
    lane.add_argument(
        "--container-only",
        action="store_true",
        help="run only Continual Harness; defaults to Glimmer then Spark",
    )
    lane.add_argument(
        "--tool-calling-only",
        action="store_true",
        help="run only the tool-calling cells; defaults to Glimmer then Spark",
    )
    parser.add_argument(
        "--combine-with",
        type=Path,
        metavar="PRIOR_FULL_RESULTS",
        help="copy cells not being rerun into the new full-results folder",
    )
    parser.add_argument(
        "--model",
        choices=("glimmer", "spark"),
        help="run only this model; requires --container-only or --tool-calling-only",
    )
    args = parser.parse_args(argv)
    rerun_lane = args.container_only or args.tool_calling_only
    if args.combine_with and (not args.full or not rerun_lane):
        parser.error("--combine-with requires --full with --container-only or --tool-calling-only")
    if args.model and not rerun_lane:
        parser.error("--model requires --container-only or --tool-calling-only")
    pilot_args: list[str] = ["--mode", "pilot" if args.pilot else "full"]
    if args.local_only:
        pilot_args.append("--local-only")
    if args.container_only:
        pilot_args.append("--container-only")
    if args.tool_calling_only:
        pilot_args.append("--tool-calling-only")
    if args.combine_with:
        pilot_args.extend(("--combine-with", str(args.combine_with)))
    if args.model:
        pilot_args.extend(("--model", args.model))
    return pilot.main(pilot_args)


if __name__ == "__main__":
    raise SystemExit(main())
