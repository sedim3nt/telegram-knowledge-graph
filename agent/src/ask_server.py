"""Ask Bridg3 — FastAPI wrapper around `claude -p`, persona from SOUL.md.

Loads SOUL.md and the vault bundle on every request so edits go live without
restarting the server. Each request is logged to agent/logs/ask-YYYY-MM-DD.jsonl
for the chat_insights feedback loop.

Run locally:
    agent/.venv/bin/python -m src.ask_server

Production: launchd loads it via agent/deploy/ask_server.plist.template, and
cloudflared exposes it at https://ask.<your-domain>. The Pages function at
site/functions/api/ask.ts proxies browser requests to the tunnel.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import LOGS_DIR, REPO_ROOT, VAULT_DIR

LOG = logging.getLogger("ask")

SOUL_PATH = REPO_ROOT / "SOUL.md"
VAULT_BUNDLE_PATH = VAULT_DIR / "_meta" / "vault-bundle.json"
DEFAULT_MODEL = os.environ.get("CLAWRYDERZ_ASK_MODEL", "sonnet").strip() or "sonnet"
# Cascade: try DEFAULT_MODEL → fallback chain on credit-balance errors. Higher
# tiers run out first on Claude Max's rolling 5-hour quota; haiku rarely does.
# Override with CLAWRYDERZ_ASK_FALLBACK_CHAIN="sonnet,haiku" or set empty to disable.
_FALLBACK_RAW = os.environ.get("CLAWRYDERZ_ASK_FALLBACK_CHAIN", "sonnet,haiku")
FALLBACK_CHAIN = [m.strip() for m in _FALLBACK_RAW.split(",") if m.strip()]
CLI_TIMEOUT_S = 120
MAX_HISTORY_TURNS = 6
MAX_QUESTION_LEN = 2000

# Hint clients to retry after the Claude Max rolling 5-hour window has had time
# to refill. Conservative — actual refill is gradual, not all-at-once.
QUOTA_RETRY_AFTER_S = 1800

# Optional shared secret. When set, requests must carry `Authorization: Bearer <secret>`.
# The Cloudflare Pages function injects it from the same env var. Empty disables auth
# (intended for `python -m src.ask_server` smoke testing on localhost).
SHARED_SECRET = os.environ.get("ASK_SHARED_SECRET", "").strip()


# ---------------------------------------------------------------------------
# Request / response schema
# ---------------------------------------------------------------------------

class HistoryTurn(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_LEN)
    history: list[HistoryTurn] = Field(default_factory=list)
    current_page: str | None = None


class AskResponse(BaseModel):
    answer: str
    request_id: str
    latency_ms: int
    model: str


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Bridg3bot Ask", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # gated by Pages middleware + tunnel auth, not CORS
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Persona + vault loaders (re-read on every request — cheap, allows live edits)
# ---------------------------------------------------------------------------

def _load_soul() -> str:
    if not SOUL_PATH.exists():
        raise RuntimeError(f"SOUL.md missing at {SOUL_PATH}")
    return SOUL_PATH.read_text(encoding="utf-8")


def _load_vault_bundle() -> str:
    """Return the vault bundle as a string (JSON content, fed to the model)."""
    if not VAULT_BUNDLE_PATH.exists():
        return ""
    return VAULT_BUNDLE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_user_prompt(
    *,
    vault_bundle: str,
    history: list[HistoryTurn],
    question: str,
    current_page: str | None,
) -> str:
    """Assemble the model input.

    Order matters for prompt caching: the vault bundle is constant within a day,
    so we put it first as a stable prefix. Per-request bits (history, question)
    come after.
    """
    parts: list[str] = []
    if vault_bundle:
        parts.append("=== VAULT (your only source of truth) ===")
        parts.append(vault_bundle)
        parts.append("=== END VAULT ===")
        parts.append("")
    if current_page:
        parts.append(f"The user is currently looking at: {current_page}")
        parts.append("")
    if history:
        parts.append("=== Conversation so far ===")
        for turn in history[-MAX_HISTORY_TURNS:]:
            label = "User" if turn.role == "user" else "Bridg3"
            parts.append(f"{label}: {turn.content}")
        parts.append("=== End conversation ===")
        parts.append("")
    parts.append(f"User: {question}")
    parts.append("Bridg3:")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Claude CLI invocation
# ---------------------------------------------------------------------------

def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    # claude CLI prefers ANTHROPIC_API_KEY over CLAUDE_CODE_OAUTH_TOKEN when both
    # are set. Under launchd we want OAuth (Claude Max) — strip the API key so the
    # OAuth token wins. (.env has ANTHROPIC_API_KEY for unrelated tools.)
    if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


class _CreditError(RuntimeError):
    """The primary model is rate-limited / out of credit on this 5-hour window."""


def _claude_call(system_prompt: str, user_prompt: str, model: str) -> str:
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--no-session-persistence",
        "--tools", "",
        "--disable-slash-commands",
        "--system-prompt", system_prompt,
    ]
    proc = subprocess.run(
        cmd, input=user_prompt, capture_output=True, text=True,
        timeout=CLI_TIMEOUT_S, env=_subprocess_env(),
    )
    if proc.returncode != 0 and not proc.stdout:
        LOG.warning("claude CLI rc=%s stderr=%s", proc.returncode, proc.stderr.strip()[:300])
        raise RuntimeError(f"claude CLI exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        result = envelope.get("result", "") or ""
        LOG.warning("claude is_error=true model=%s result=%r stderr=%r",
                    model, result[:300], proc.stderr.strip()[:200])
        lc = result.lower()
        if "credit balance" in lc or "too low" in lc or "rate" in lc and "limit" in lc:
            raise _CreditError(result[:300])
        raise RuntimeError(f"claude error: {result[:300]}")
    return (envelope.get("result") or "").strip()


def _claude_call_with_fallback(
    system_prompt: str, user_prompt: str, primary: str, fallback_chain: list[str],
) -> tuple[str, str]:
    """Try `primary`, then each model in `fallback_chain` in order.

    Only credit-balance errors cause a fallback; real errors propagate up.
    Returns (answer_text, model_actually_used).
    """
    tried: list[str] = []
    seen: set[str] = set()
    for model in [primary, *fallback_chain]:
        if not model or model in seen:
            continue
        seen.add(model)
        try:
            answer = _claude_call(system_prompt, user_prompt, model)
            if tried:
                LOG.warning("ask: primary models %s exhausted; served via %s", tried, model)
            return answer, model
        except _CreditError:
            tried.append(model)
            continue
    raise _CreditError(
        f"all models exhausted: tried {tried}. Quota will refresh on the "
        "Claude Max rolling 5-hour window."
    )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log_request(payload: dict[str, Any]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"ask-{today}.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _check_auth(request: Request) -> None:
    if not SHARED_SECRET:
        return
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth[7:].strip()
    # Constant-time-ish comparison
    if len(token) != len(SHARED_SECRET) or token != SHARED_SECRET:
        raise HTTPException(status_code=403, detail="invalid token")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "soul_loaded": SOUL_PATH.exists(),
        "vault_bundle_loaded": VAULT_BUNDLE_PATH.exists(),
        "model": DEFAULT_MODEL,
        "auth_required": bool(SHARED_SECRET),
    }


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request) -> AskResponse:
    _check_auth(request)
    started = time.time()
    request_id = uuid.uuid4().hex[:12]
    client_ip = request.headers.get(
        "x-forwarded-for",
        request.client.host if request.client else "?",
    )

    soul = _load_soul()
    vault_bundle = _load_vault_bundle()
    user_prompt = _build_user_prompt(
        vault_bundle=vault_bundle,
        history=req.history,
        question=req.question,
        current_page=req.current_page,
    )

    err: str | None = None
    answer = ""
    used_model = DEFAULT_MODEL
    try:
        answer, used_model = _claude_call_with_fallback(
            soul, user_prompt, DEFAULT_MODEL, FALLBACK_CHAIN,
        )
        return AskResponse(
            answer=answer,
            request_id=request_id,
            latency_ms=int((time.time() - started) * 1000),
            model=used_model,
        )
    except subprocess.TimeoutExpired:
        err = "timeout"
        raise HTTPException(status_code=504, detail="claude CLI timeout")
    except _CreditError as ce:
        err = f"quota_exhausted: {str(ce)[:200]}"
        LOG.warning("ask: quota exhausted across all models — returning 503")
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": str(QUOTA_RETRY_AFTER_S)},
            content={
                "error": "quota_exhausted",
                "message": (
                    "Bridg3 is catching his breath — we've burned through today's "
                    "Claude quota. The 5-hour rolling window is replenishing now; "
                    "try again in about 30 minutes."
                ),
                "retry_after_seconds": QUOTA_RETRY_AFTER_S,
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        err = str(e)[:300]
        LOG.exception("ask failed")
        raise HTTPException(status_code=500, detail=err)
    finally:
        _log_request({
            "ts": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "ip": client_ip,
            "current_page": req.current_page,
            "question": req.question,
            "history_turns": len(req.history),
            "answer": answer if answer else None,
            "model": used_model,
            "primary_model": DEFAULT_MODEL,
            "latency_ms": int((time.time() - started) * 1000),
            "error": err,
        })


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    host = os.environ.get("ASK_HOST", "127.0.0.1")
    port = int(os.environ.get("ASK_PORT", "8787"))
    LOG.info("ask_server: starting on %s:%d (auth=%s, model=%s)",
             host, port, "on" if SHARED_SECRET else "off", DEFAULT_MODEL)
    uvicorn.run(
        "src.ask_server:app",
        host=host, port=port, log_level="info",
    )


if __name__ == "__main__":
    main()
