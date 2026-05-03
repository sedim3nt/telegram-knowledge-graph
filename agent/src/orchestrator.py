"""Knowledge-graph orchestrator — one-shot run, scheduled by macOS launchd.

Usage:
  python -m src.orchestrator             # live run
  python -m src.orchestrator --dry-run   # parse + log; no DB writes, no notify
"""
from __future__ import annotations

import argparse
import fcntl
import logging
import sys
import time
from datetime import datetime, timezone

import subprocess

from . import classify, concept, graph, person, render, summarize
from .config import Config, DATA_DIR, LOGS_DIR, REPO_ROOT, VAULT_DIR
from .notify import format_run_summary, send_owner
from .poll import fetch_updates

LOG = logging.getLogger("orchestrator")
LOCK_PATH = DATA_DIR / "orchestrator.lock"


def _setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"run-{today}.log"
    fmt = "%(asctime)s %(levelname)s %(name)s | %(message)s"
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _acquire_lock():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        LOG.error("another orchestrator run is in progress; exiting")
        sys.exit(2)
    return fh


def run_once(*, dry_run: bool = False) -> dict:
    cfg = Config.load()
    LOG.info(
        "loaded config: bot=@%s chat=%s owner_dm=%s",
        cfg.bridg3bot_username,
        cfg.bridg3bot_chat_id,
        "set" if cfg.owner_chat_id else "unset",
    )

    started = time.time()
    stats = fetch_updates(cfg, dry_run=dry_run)

    # Cheap classifier pass over any newly-ingested atomics. Best-effort: failures
    # are logged but don't fail the whole run.
    if not dry_run:
        try:
            stats["classify"] = classify.classify_pending()
        except Exception as e:  # noqa: BLE001
            LOG.warning("classify phase failed: %s", e)
            stats["classify"] = {"error": str(e)}

        # Concept + person synthesis, render, graph. Each phase is best-effort:
        # if one fails we log and continue so partial progress isn't lost.
        try:
            stats["concepts"] = concept.synthesize()
        except Exception as e:  # noqa: BLE001
            LOG.warning("concept synthesis failed: %s", e)
            stats["concepts"] = {"error": str(e)}
        try:
            stats["persons"] = person.synthesize()
        except Exception as e:  # noqa: BLE001
            LOG.warning("person synthesis failed: %s", e)
            stats["persons"] = {"error": str(e)}
        try:
            # Hash-cached: only re-summarizes concepts/people whose source atoms
            # changed since the last run. First run is heavy; subsequent runs
            # touch only what's new.
            stats["summarize"] = summarize.synthesize()
        except Exception as e:  # noqa: BLE001
            LOG.warning("summarize phase failed: %s", e)
            stats["summarize"] = {"error": str(e)}
        try:
            stats["render"] = render.render_all()
        except Exception as e:  # noqa: BLE001
            LOG.warning("render failed: %s", e)
            stats["render"] = {"error": str(e)}
        try:
            stats["graph"] = graph.compute()
        except Exception as e:  # noqa: BLE001
            LOG.warning("graph compute failed: %s", e)
            stats["graph"] = {"error": str(e)}

        # Push vault changes to GitHub. Cloudflare Pages auto-builds on push.
        try:
            stats["git"] = _git_push_vault(cfg)
        except Exception as e:  # noqa: BLE001
            LOG.warning("git push failed: %s", e)
            stats["git"] = {"error": str(e)}

    stats["duration_s"] = round(time.time() - started, 2)
    LOG.info("run finished: %s", stats)

    summary = format_run_summary(stats, dry_run=dry_run)
    print("\n" + summary + "\n")

    if not dry_run:
        send_owner(cfg, summary)

    return stats


def _git_push_vault(cfg: Config) -> dict:
    """Commit any vault/ changes and push to origin. No-op if no diff or no remote."""
    repo = REPO_ROOT
    # Only act on changes inside vault/ (atomic + classify data is gitignored).
    diff = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "vault/"],
        capture_output=True, text=True, check=False,
    )
    if not diff.stdout.strip():
        return {"committed": False, "reason": "no vault changes"}

    add = subprocess.run(
        ["git", "-C", str(repo), "add", "vault/"],
        capture_output=True, text=True, check=False,
    )
    if add.returncode != 0:
        return {"committed": False, "error": f"git add failed: {add.stderr.strip()[:200]}"}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    msg = f"vault: nightly sync {today}\n\n🤖 Auto-generated by knowledge-graph orchestrator."
    commit = subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", msg],
        capture_output=True, text=True, check=False,
    )
    if commit.returncode != 0:
        return {"committed": False, "error": f"git commit failed: {commit.stderr.strip()[:200]}"}

    # Push only if a remote exists; otherwise just commit and return.
    remote_check = subprocess.run(
        ["git", "-C", str(repo), "remote"],
        capture_output=True, text=True, check=False,
    )
    if not remote_check.stdout.strip():
        return {"committed": True, "pushed": False, "reason": "no git remote configured"}

    push = subprocess.run(
        ["git", "-C", str(repo), "push"],
        capture_output=True, text=True, check=False,
    )
    if push.returncode != 0:
        return {"committed": True, "pushed": False, "error": push.stderr.strip()[:200]}

    return {"committed": True, "pushed": True}


def main() -> int:
    parser = argparse.ArgumentParser(prog="tkg-orchestrator")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + log, but do not write to DB/JSONL or send notifications.",
    )
    args = parser.parse_args()

    _setup_logging()
    lock = _acquire_lock()
    try:
        run_once(dry_run=args.dry_run)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        LOG.exception("orchestrator failed: %s", e)
        try:
            cfg = Config.load()
            send_owner(cfg, f"ERROR knowledge-graph run FAILED\n{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            pass
        return 1
    finally:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
        except Exception:  # noqa: BLE001
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
