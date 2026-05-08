"""Idempotent setup wizard for ClawRyderz.

Run from the repo root:
    python3 agent/scripts/setup_wizard.py

Steps:
  1. Verify Python >= 3.11
  2. Create agent/.venv if missing; install deps
  3. Sanity-check .env required keys; prompt for missing ones
  4. Validate Bridg3bot token via Telegram getMe
  5. Optionally capture TELEGRAM_BRIDG3BOT_CHAT_ID by polling getUpdates
  6. Optionally run a --dry-run smoke test
  7. Optionally install the launchd LaunchAgent (macOS)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "agent"
VENV_DIR = AGENT_DIR / ".venv"
PYTHON_BIN = VENV_DIR / "bin" / "python"
ENV_PATH = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
PLIST_TEMPLATE = AGENT_DIR / "deploy" / "launchd.plist.template"
ASK_PLIST_TEMPLATE = AGENT_DIR / "deploy" / "ask_server.plist.template"
CFD_PLIST_TEMPLATE = AGENT_DIR / "deploy" / "cloudflared.plist.template"
CFD_CONFIG_PATH = AGENT_DIR / "deploy" / "cloudflared-config.yml"
# Label is derived from the directory name so each fork installs as its own job.
# The Ask Bridg3 server + cloudflared get suffixed labels so all three jobs
# can coexist without colliding.
_LABEL_BASE = f"ai.clawryderz.{REPO_ROOT.name.lower().replace('_', '-')}"
LAUNCHD_LABEL = f"ai.tkg.{REPO_ROOT.name.lower().replace(chr(95), chr(45))}"
ASK_LAUNCHD_LABEL = f"{_LABEL_BASE}.ask"
CFD_LAUNCHD_LABEL = f"{_LABEL_BASE}.cloudflared"
PLIST_DST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
ASK_PLIST_DST = Path.home() / "Library" / "LaunchAgents" / f"{ASK_LAUNCHD_LABEL}.plist"
CFD_PLIST_DST = Path.home() / "Library" / "LaunchAgents" / f"{CFD_LAUNCHD_LABEL}.plist"

REQUIRED_KEYS = ["TELEGRAM_BRIDG3BOT_TOKEN"]
OPTIONAL_KEYS = [
    "TELEGRAM_BRIDG3BOT_USERNAME",
    "TELEGRAM_BRIDG3BOT_CHAT_ID",
    "TELEGRAM_OWNER_CHAT_ID",
    "ANTHROPIC_API_KEY",
    "KIMI_API_KEY",
    "SITE_PASSWORD",
    "ASK_SHARED_SECRET",
]


# ---------- .env helpers (preserve order + comments) ----------

def load_env() -> tuple[list[str], dict[str, str]]:
    if not ENV_PATH.exists():
        if ENV_EXAMPLE.exists():
            ENV_PATH.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  Created {ENV_PATH} from .env.example")
        else:
            ENV_PATH.touch()
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        values[key.strip()] = val.strip().strip("'\"")
    return lines, values


def write_env_value(key: str, value: str) -> None:
    """Update or append KEY=value, preserving file order + comments."""
    lines, _ = load_env()
    new_line = f"{key}={value}"
    found = False
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        existing_key, _, _ = s.partition("=")
        if existing_key.strip() == key:
            lines[i] = new_line
            found = True
            break
    if not found:
        lines.append(new_line)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prompt(label: str, default: str = "", *, secret: bool = False) -> str:
    suffix = ""
    if default:
        shown = ("•" * 6) if secret else default
        suffix = f" [{shown}]"
    raw = input(f"{label}{suffix}: ").strip()
    return raw or default


def yesno(label: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{label} ({suffix}): ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# ---------- Telegram helpers ----------

def telegram_get(method: str, token: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "tkg-setup/1.0"})
    with urlopen(req, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


# ---------- Steps ----------

def step_python() -> None:
    if sys.version_info < (3, 11):
        sys.exit(f"Python 3.11+ required (you have {sys.version})")
    print(f"OK Python {sys.version.split()[0]}")


def step_venv() -> None:
    if not VENV_DIR.exists():
        print(f"  Creating venv at {VENV_DIR} ...")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    if not PYTHON_BIN.exists():
        sys.exit(f"venv missing python at {PYTHON_BIN}")
    print(f"OK venv at {VENV_DIR}")
    if yesno("  Run `pip install -e agent` now?", True):
        subprocess.check_call(
            [str(PYTHON_BIN), "-m", "pip", "install", "--upgrade", "pip", "--quiet"]
        )
        subprocess.check_call(
            [str(PYTHON_BIN), "-m", "pip", "install", "-e", str(AGENT_DIR), "--quiet"]
        )
        print("  OK deps installed")


def step_env_keys() -> None:
    _, env = load_env()
    print("\nRequired keys:")
    for key in REQUIRED_KEYS:
        if env.get(key):
            print(f"  OK {key} present")
        else:
            new = prompt(f"  Missing {key}", "", secret=True)
            if not new:
                sys.exit(f"  {key} is required.")
            write_env_value(key, new)
            print(f"  OK wrote {key}")
    print("\nOptional keys (Enter to skip):")
    for key in OPTIONAL_KEYS:
        if env.get(key):
            print(f"  OK {key} present")
            continue
        new = prompt(f"  {key}", "", secret=True)
        if new:
            write_env_value(key, new)
            print(f"    OK wrote {key}")


def step_validate_token() -> None:
    _, env = load_env()
    token = env.get("TELEGRAM_BRIDG3BOT_TOKEN", "")
    if not token:
        print("WARN skipping token check (no token set)")
        return
    try:
        body = telegram_get("getMe", token)
    except (URLError, HTTPError) as e:
        sys.exit(f"  ERR getMe failed: {e}")
    if not body.get("ok"):
        sys.exit(f"  ERR Telegram returned not-ok: {body}")
    me = body["result"]
    print(f"  OK token works -> @{me['username']} (id={me['id']})")
    if not me.get("can_read_all_group_messages"):
        print(
            "  WARN privacy mode is ENABLED. Set BotFather -> /setprivacy -> Disable\n"
            "       BEFORE adding the bot to the channel (Telegram only re-applies\n"
            "       privacy mode at join time)."
        )
    owner_chat = env.get("TELEGRAM_OWNER_CHAT_ID")
    if owner_chat:
        print(
            f"  NOTE if owner DMs to chat_id={owner_chat} fail with 403,\n"
            f"       send '/start' to @{me['username']} once. Bot won't reply\n"
            f"       (no command handler) but this enables owner DM pings."
        )


def step_capture_chat_id() -> None:
    _, env = load_env()
    if env.get("TELEGRAM_BRIDG3BOT_CHAT_ID"):
        print(f"  OK TELEGRAM_BRIDG3BOT_CHAT_ID already set: {env['TELEGRAM_BRIDG3BOT_CHAT_ID']}")
        return
    if not yesno(
        "  Capture chat_id now? (Bot must be in the channel and a test message posted)",
        False,
    ):
        return
    token = env.get("TELEGRAM_BRIDG3BOT_TOKEN", "")
    if not token:
        print("  WARN no token; skipping")
        return
    try:
        body = telegram_get("getUpdates", token, timeout=2)
    except (URLError, HTTPError) as e:
        print(f"  ERR getUpdates failed: {e}")
        return
    chats: dict[int, str] = {}
    for upd in body.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or upd.get("edited_message")
        if not msg:
            continue
        chat = msg.get("chat", {})
        chats[int(chat["id"])] = chat.get("title") or chat.get("username") or "(no title)"
    if not chats:
        print("  WARN no chats found in recent updates. Post a message in the channel and retry.")
        return
    print("  Discovered chats:")
    items = list(chats.items())
    for i, (cid, title) in enumerate(items, 1):
        print(f"    {i}) {cid}  {title}")
    pick = prompt("  Pick number to set as TELEGRAM_BRIDG3BOT_CHAT_ID", "1")
    try:
        cid = items[int(pick) - 1][0]
    except (ValueError, IndexError):
        print("  ERR invalid pick")
        return
    write_env_value("TELEGRAM_BRIDG3BOT_CHAT_ID", str(cid))
    print(f"  OK wrote TELEGRAM_BRIDG3BOT_CHAT_ID={cid}")


def step_dry_run() -> None:
    if not yesno("  Run a --dry-run smoke test now?", True):
        return
    subprocess.check_call(
        [str(PYTHON_BIN), "-m", "src.orchestrator", "--dry-run"], cwd=str(AGENT_DIR)
    )


def _install_plist(template: Path, dst: Path, label: str, *, extra: dict[str, str] | None = None) -> None:
    rendered = template.read_text().replace("{{REPO_ROOT}}", str(REPO_ROOT)).replace("{{LABEL}}", label)
    for k, v in (extra or {}).items():
        rendered = rendered.replace(k, v)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(rendered)
    subprocess.run(["launchctl", "unload", str(dst)], check=False, capture_output=True)
    subprocess.check_call(["launchctl", "load", str(dst)])


def step_launchd() -> None:
    if sys.platform != "darwin":
        print("  (non-macOS — skipping launchd; on Linux use cron)")
        return
    if not yesno(f"  Install nightly orchestrator launchd job at {PLIST_DST}?", True):
        return
    if not PLIST_TEMPLATE.exists():
        print(f"  ERR plist template missing: {PLIST_TEMPLATE}")
        return
    _install_plist(PLIST_TEMPLATE, PLIST_DST, LAUNCHD_LABEL)
    print(f"  OK loaded {PLIST_DST}")
    print(f"    Label: {LAUNCHD_LABEL}")
    print("    Next run: 04:00 local time. Manual fire:")
    print("    env -u CLAUDECODE agent/.venv/bin/python -m src.orchestrator")


def step_ask_server_launchd() -> None:
    """Install the Ask Bridg3 FastAPI long-lived job."""
    if sys.platform != "darwin":
        return
    if not ASK_PLIST_TEMPLATE.exists():
        return
    if not yesno(f"\n  Install Ask Bridg3 FastAPI job at {ASK_PLIST_DST}?", True):
        return
    _install_plist(ASK_PLIST_TEMPLATE, ASK_PLIST_DST, ASK_LAUNCHD_LABEL)
    print(f"  OK loaded {ASK_PLIST_DST}")
    print(f"    Label: {ASK_LAUNCHD_LABEL}")
    print("    Health check: curl -s http://127.0.0.1:8787/health")


def step_cloudflared_launchd() -> None:
    """Install the cloudflared tunnel that exposes ask_server.py to the public.

    Skipped if cloudflared isn't on PATH or no cloudflared-config.yml exists yet
    (the user has to run `cloudflared tunnel create` + DNS routing first).
    """
    if sys.platform != "darwin":
        return
    if not CFD_PLIST_TEMPLATE.exists():
        return
    cloudflared_bin = ""
    for candidate in ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared"):
        if Path(candidate).exists():
            cloudflared_bin = candidate
            break
    if not cloudflared_bin:
        from_path = subprocess.run(["which", "cloudflared"], capture_output=True, text=True, check=False)
        cloudflared_bin = from_path.stdout.strip()
    if not cloudflared_bin:
        print("\n  (cloudflared not found on PATH — skipping tunnel job.")
        print("   Install it: `brew install cloudflared`, then re-run this wizard.)")
        return
    if not CFD_CONFIG_PATH.exists():
        print(f"\n  (cloudflared-config.yml missing at {CFD_CONFIG_PATH} — skipping tunnel job.")
        print("   First run: cloudflared tunnel login && cloudflared tunnel create ask-bridg3")
        print("   Then: cloudflared tunnel route dns ask-bridg3 ask.<your-domain>")
        print("   Then: cp agent/deploy/cloudflared-config.example.yml agent/deploy/cloudflared-config.yml")
        print("   Edit cloudflared-config.yml with your tunnel UUID + hostname, then re-run this wizard.)")
        return
    if not yesno(f"\n  Install cloudflared tunnel launchd job at {CFD_PLIST_DST}?", True):
        return
    _install_plist(
        CFD_PLIST_TEMPLATE, CFD_PLIST_DST, CFD_LAUNCHD_LABEL,
        extra={"{{CLOUDFLARED_BIN}}": cloudflared_bin},
    )
    print(f"  OK loaded {CFD_PLIST_DST}")
    print(f"    Label: {CFD_LAUNCHD_LABEL}")
    print(f"    Binary: {cloudflared_bin}")
    print(f"    Config: {CFD_CONFIG_PATH}")


def main() -> None:
    print("\n== telegram knowledge-graph setup wizard ==\n")
    step_python()
    step_venv()
    step_env_keys()
    step_validate_token()
    step_capture_chat_id()
    step_dry_run()
    step_launchd()
    step_ask_server_launchd()
    step_cloudflared_launchd()
    print("\nOK setup complete.\n")


if __name__ == "__main__":
    main()
