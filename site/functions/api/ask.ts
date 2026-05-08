/**
 * Cloudflare Pages Function — proxy for the Ask Bridg3 chat endpoint.
 *
 * Browser → POST /api/ask → this proxy → cloudflared tunnel →
 *   FastAPI ask_server.py running on the Mac mini → claude -p (Sonnet).
 *
 * Why proxy at all?
 *   1. Hides the tunnel URL behind the same auth as the rest of the site
 *      (the parent _middleware.ts has already validated the cookie before this
 *      function runs).
 *   2. Lets us inject the shared bearer token without exposing it in the
 *      browser bundle.
 *   3. Single-origin requests, so no CORS dance.
 *
 * Required Pages env vars:
 *   ASK_TUNNEL_URL      e.g. https://ask-clawryderz.spirittree.dev
 *   ASK_SHARED_SECRET   matches ASK_SHARED_SECRET on the FastAPI server
 */

interface Env {
  ASK_TUNNEL_URL?: string;
  ASK_SHARED_SECRET?: string;
}

const UPSTREAM_TIMEOUT_MS = 120_000;

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  const tunnel = (env.ASK_TUNNEL_URL ?? "").trim();
  if (!tunnel) {
    return jsonError(503, "ASK_TUNNEL_URL not configured on Pages project");
  }

  let raw: string;
  try {
    raw = await request.text();
  } catch {
    return jsonError(400, "could not read request body");
  }

  // Minimal shape validation. Server validates fully via Pydantic.
  let parsed: any;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return jsonError(400, "invalid JSON");
  }
  if (typeof parsed?.question !== "string" || !parsed.question.trim()) {
    return jsonError(400, "missing 'question'");
  }
  if (parsed.question.length > 2000) {
    return jsonError(400, "question too long (max 2000 chars)");
  }

  const headers: HeadersInit = { "Content-Type": "application/json" };
  if (env.ASK_SHARED_SECRET) {
    headers.Authorization = `Bearer ${env.ASK_SHARED_SECRET}`;
  }

  const target = `${tunnel.replace(/\/+$/, "")}/ask`;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: "POST",
      headers,
      body: raw,
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch (e) {
    return jsonError(502, `upstream unreachable: ${stringify(e)}`);
  }

  const upstreamBody = await upstream.text();
  return new Response(upstreamBody, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("Content-Type") ?? "application/json",
      "Cache-Control": "no-store",
    },
  });
};

// GET → 405 (rather than render the login page accidentally)
export const onRequestGet: PagesFunction<Env> = async () => {
  return jsonError(405, "method not allowed; POST /api/ask");
};

function jsonError(status: number, error: string): Response {
  return new Response(JSON.stringify({ error }), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

function stringify(e: unknown): string {
  if (e instanceof Error) return e.message;
  try {
    return String(e);
  } catch {
    return "unknown error";
  }
}
