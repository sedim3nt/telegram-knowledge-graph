"""End-to-end vault synthesis driver: concept → person → render → graph.

Run after canonicalize_topics.py + resolve_persons.py have produced the maps.

    agent/.venv/bin/python agent/scripts/synthesize_vault.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agent"))

from src import concept, graph, person, render  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def main() -> int:
    print("== Phase 1: concept synthesis ==")
    cstats = concept.synthesize()
    print(f"  → {cstats}")
    print()
    print("== Phase 2: person synthesis ==")
    pstats = person.synthesize()
    print(f"  → {pstats}")
    print()
    print("== Phase 3: markdown rendering ==")
    rstats = render.render_all()
    print(f"  → {rstats}")
    print()
    print("== Phase 4: graph computation ==")
    gstats = graph.compute()
    print(f"  → {gstats}")
    print()
    print("DONE. Vault at:", REPO_ROOT / "vault")
    return 0


if __name__ == "__main__":
    sys.exit(main())
