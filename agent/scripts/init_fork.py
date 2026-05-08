"""Initialize a fresh ClawRyderz fork for a new Telegram channel.

Wipes the inherited vault data (concepts/people/graph from the original
ClawRyderz instance) and clears the local-only data caches so you start
clean with your own channel's content.

Run ONCE, immediately after cloning the repo for your own channel.

Usage:
    agent/.venv/bin/python agent/scripts/init_fork.py

This is destructive but only touches data files that should be regenerated
from your own ingestion. The CODE (agent/, site/, scripts/, configs) is
preserved intact.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def confirm(prompt: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{prompt} ({suffix}): ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def wipe_dir_contents(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for child in path.iterdir():
        if child.name in (".gitkeep", ".keep"):
            continue
        if child.is_file():
            child.unlink()
            count += 1
        elif child.is_dir():
            shutil.rmtree(child)
            count += 1
    return count


def _check_not_original_instance() -> None:
    """Refuse to run on the canonical sedim3nt/clawryderz repo (would wipe live data)."""
    import subprocess as sp
    try:
        remote = sp.check_output(
            ["git", "-C", str(REPO_ROOT), "remote", "get-url", "origin"],
            text=True, stderr=sp.DEVNULL,
        ).strip()
    except Exception:
        return  # no remote configured = fork in progress, fine
    if "sedim3nt/clawryderz" in remote:
        print("\nERROR: This appears to be the original ClawRyderz instance")
        print(f"       (origin = {remote}).")
        print("       init_fork.py would wipe the live channel's data.")
        print()
        print("If you really meant to do this:")
        print("  1. Change the GitHub remote first:")
        print(f"     git -C {REPO_ROOT} remote set-url origin <your-fork-url>")
        print("  2. Then re-run this script.")
        sys.exit(2)


def main() -> int:
    _check_not_original_instance()
    print("\n══ ClawRyderz fork initialization ══\n")
    print("This will WIPE the published vault content (the original channel's data)")
    print("and the local data caches. Your code, configs, and Quartz site stay intact.\n")
    print(f"Repo: {REPO_ROOT}\n")

    targets = [
        ("vault/concepts (synthesized concept pages)", REPO_ROOT / "vault" / "concepts"),
        ("vault/people (contributor pages)",            REPO_ROOT / "vault" / "people"),
        ("vault/_meta (graph + index)",                 REPO_ROOT / "vault" / "_meta"),
        ("agent/data/atomic (raw ingested messages)",   REPO_ROOT / "agent" / "data" / "atomic"),
        ("agent/data/classify (Haiku tags)",            REPO_ROOT / "agent" / "data" / "classify"),
        ("agent/data/ingest (JSONL audit trail)",       REPO_ROOT / "agent" / "data" / "ingest"),
    ]
    other_files = [
        REPO_ROOT / "agent" / "data" / "state.db",
        REPO_ROOT / "agent" / "data" / "telethon.session",
        REPO_ROOT / "agent" / "data" / "canonical_topics.json",
        REPO_ROOT / "agent" / "data" / "person_aliases.json",
        REPO_ROOT / "agent" / "data" / "topics.json",
    ]

    for label, path in targets:
        if path.exists():
            print(f"  • {label}: contains {sum(1 for _ in path.iterdir())} entries")
    for f in other_files:
        if f.exists():
            print(f"  • {f.relative_to(REPO_ROOT)}: {f.stat().st_size} bytes")

    print()
    if not confirm("Proceed with wipe?", default=False):
        print("Aborted. Nothing changed.")
        return 0

    total = 0
    for label, path in targets:
        n = wipe_dir_contents(path)
        if n:
            print(f"  ✓ wiped {label} ({n} entries)")
        total += n

    for f in other_files:
        if f.exists():
            f.unlink()
            print(f"  ✓ removed {f.relative_to(REPO_ROOT)}")
            total += 1

    # Restore a placeholder index.md so the site still has a homepage
    index_path = REPO_ROOT / "vault" / "index.md"
    index_path.write_text(
        "---\ntitle: Knowledge Vault\n---\n\n"
        "# Knowledge Vault\n\n"
        "_Awaiting first ingestion. Run setup_wizard.py + your first orchestrator run, "
        "then this page can be customized._\n",
        encoding="utf-8",
    )
    print(f"  ✓ reset vault/index.md placeholder")

    print(f"\n══ Wiped {total} items. ══")
    print("\nNext steps:")
    print("  1. Edit .env with YOUR Telegram bot token, chat_id, etc.")
    print("  2. Run: agent/.venv/bin/python agent/scripts/setup_wizard.py")
    print("  3. Run Telethon backfill if you want history:")
    print("     agent/.venv/bin/python agent/scripts/backfill_telethon.py")
    print("  4. First orchestrator run:")
    print("     env -u CLAUDECODE agent/.venv/bin/python -m src.orchestrator")
    print("  5. After atoms ingested, run canonicalize + person resolution:")
    print("     agent/.venv/bin/python agent/scripts/canonicalize_topics.py")
    print("     agent/.venv/bin/python agent/scripts/resolve_persons.py")
    print("  6. Re-run orchestrator to synthesize + summarize + render + push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
