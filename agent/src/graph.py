"""Compute vault/_meta/graph.json from concept and person records.

Two node types: `concept` (large) and `person` (small).
Atoms are NOT nodes — would swamp the graph.

Edge kinds:
  concept ↔ concept:  related (co-occurrence)
  concept → concept:  supersedes (whole-concept supersession; v2 feature, not yet)
  person → concept:   originated | consensus-builder
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import VAULT_DIR

LOG = logging.getLogger("graph")

CONCEPTS_DIR = VAULT_DIR / "concepts"
PEOPLE_DIR = VAULT_DIR / "people"
META_DIR = VAULT_DIR / "_meta"
GRAPH_PATH = META_DIR / "graph.json"

SCHEMA = "graph.v1"


def _node_size_concept(atom_count: int) -> int:
    # Log-ish scaling: more weight without dwarfing small concepts
    if atom_count <= 0:
        return 8
    return min(64, 8 + int((atom_count) ** 0.6))


def _node_size_person(total_messages: int) -> int:
    if total_messages <= 0:
        return 4
    return min(32, 4 + int((total_messages) ** 0.5))


def compute() -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []

    # Concept nodes
    concepts: dict[str, dict] = {}
    for p in CONCEPTS_DIR.glob("*.json"):
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        cid = c["concept_id"]
        concepts[cid] = c
        nodes.append({
            "id": f"concept:{cid}",
            "type": "concept",
            "label": c["title"],
            "category": c.get("category", "other"),
            "status": c.get("status", "stable"),
            "current_version": c.get("current_version"),
            "atom_count": c.get("atom_count", 0),
            "size": _node_size_concept(c.get("atom_count", 0)),
            "first_seen": c.get("first_seen"),
            "last_updated": c.get("last_updated"),
            "url": f"concepts/{cid}",
        })

    # Person nodes
    persons: dict[str, dict] = {}
    for p in PEOPLE_DIR.glob("*.json"):
        try:
            person = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        username = person.get("username") or p.stem
        if not username:
            continue
        persons[username] = person
        nodes.append({
            "id": f"person:{username}",
            "type": "person",
            "label": f"@{username}",
            "display_name": person.get("display_name"),
            "is_bot": bool(person.get("is_bot") or person.get("is_bot_persona")),
            "external": bool(person.get("external")),
            "total_messages": person.get("total_messages", 0),
            "size": _node_size_person(person.get("total_messages", 0)),
            "url": f"people/{p.stem}",
        })

    # Concept ↔ concept "related" edges (from .related field, deduped pair-wise)
    seen_pairs: set[tuple[str, str]] = set()
    for cid, c in concepts.items():
        for other in c.get("related", []) or []:
            if other not in concepts:
                continue
            pair = tuple(sorted([cid, other]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            edges.append({
                "source": f"concept:{pair[0]}",
                "target": f"concept:{pair[1]}",
                "kind": "related",
                "weight": 0.5,
            })

    # Person → concept "originated" / "consensus-builder" edges
    for cid, c in concepts.items():
        for contrib in c.get("contributors", []) or []:
            handle = contrib.get("handle")
            if not handle:
                continue
            # Match handle directly (people are stored under their canonical username)
            edges.append({
                "source": f"person:{handle}",
                "target": f"concept:{cid}",
                "kind": contrib.get("role", "consensus-builder"),
                "weight": min(1.0, contrib.get("msg_count", 1) / 20.0),
            })

    META_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "$schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }
    GRAPH_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("graph: %d nodes, %d edges → %s", len(nodes), len(edges), GRAPH_PATH)
    return {"nodes": len(nodes), "edges": len(edges), "path": str(GRAPH_PATH)}


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    print(compute())
