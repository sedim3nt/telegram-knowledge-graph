"""Force-regenerate every concept + person summary.

Usage:
    agent/.venv/bin/python agent/scripts/refresh_summaries.py        # honor thresholds (rare-call use)
    agent/.venv/bin/python agent/scripts/refresh_summaries.py --force  # regenerate ALL

Use cases for --force:
  - You tweaked the system prompt in summarize.py and want every page rebuilt
    with the new wording
  - You switched models (e.g. sonnet -> opus) and want consistent voice
  - First-time bootstrap (already done — see commit 9d29fb4)

Without --force, this is identical to what the nightly orchestrator runs:
honors the per-concept / per-person thresholds (see CONCEPT_DELTA_THRESHOLD,
PERSON_DELTA_THRESHOLD, STALE_DAYS in agent/src/summarize.py).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agent"))

from src import summarize  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(prog="refresh_summaries")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate every concept and person summary, ignoring thresholds.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    stats = summarize.synthesize(force=args.force)
    print()
    print("DONE.", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
