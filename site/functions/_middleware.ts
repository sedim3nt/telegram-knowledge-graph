/**
 * Cloudflare Pages Function — cookie-based auth middleware with a styled login form.
 *
 * Replaces HTTP Basic Auth so we can render a real branded login screen instead
 * of the browser's native prompt (which is unstyleable).
 *
 * Flow:
 *   GET  /login         → render styled form
 *   POST /login         → validate, set HMAC-signed session cookie, 302 to ?next or /
 *   any other request   → check cookie. valid? next(). missing/bad? 302 to /login?next=…
 *
 * Cookie:
 *   - name    : clr_session
 *   - value   : `${expMs}.${base64url(hmac_sha256(SITE_PASSWORD, "ryder|"+expMs))}`
 *   - flags   : HttpOnly, Secure, SameSite=Strict, Path=/
 *   - TTL     : 30 days
 *
 * Required env vars (set in Cloudflare Pages → Settings → Environment variables):
 *   SITE_USERNAME   (default "ryder" if unset)
 *   SITE_PASSWORD   (required; also used as the HMAC secret — same value as
 *                    in your local .env)
 */

interface Env {
  SITE_USERNAME?: string;
  SITE_PASSWORD?: string;
}

const COOKIE_NAME = "clr_session";
const COOKIE_TTL_DAYS = 30;
const COOKIE_TTL_MS = COOKIE_TTL_DAYS * 24 * 60 * 60 * 1000;

// ---------------------------------------------------------------------------
// Crypto helpers (Web Crypto API, available in Cloudflare Workers runtime)
// ---------------------------------------------------------------------------

async function hmacSign(secret: string, payload: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return base64url(new Uint8Array(sig));
}

function base64url(bytes: Uint8Array): string {
  let str = "";
  for (let i = 0; i < bytes.length; i++) str += String.fromCharCode(bytes[i]);
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let mismatch = 0;
  for (let i = 0; i < a.length; i++) mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return mismatch === 0;
}

async function makeCookieValue(user: string, secret: string): Promise<string> {
  const exp = Date.now() + COOKIE_TTL_MS;
  const payload = `${user}|${exp}`;
  const sig = await hmacSign(secret, payload);
  return `${exp}.${sig}`;
}

async function verifyCookieValue(value: string, user: string, secret: string): Promise<boolean> {
  const dot = value.indexOf(".");
  if (dot === -1) return false;
  const expStr = value.slice(0, dot);
  const sig = value.slice(dot + 1);
  const exp = parseInt(expStr, 10);
  if (!Number.isFinite(exp) || exp < Date.now()) return false;
  const expected = await hmacSign(secret, `${user}|${expStr}`);
  return timingSafeEqual(sig, expected);
}

// ---------------------------------------------------------------------------
// Cookie parsing
// ---------------------------------------------------------------------------

function getCookie(request: Request, name: string): string | null {
  const header = request.headers.get("Cookie");
  if (!header) return null;
  for (const part of header.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return rest.join("=");
  }
  return null;
}

function setCookieHeader(value: string): string {
  const maxAge = COOKIE_TTL_DAYS * 24 * 60 * 60;
  return [
    `${COOKIE_NAME}=${value}`,
    `Path=/`,
    `Max-Age=${maxAge}`,
    `HttpOnly`,
    `Secure`,
    `SameSite=Strict`,
  ].join("; ");
}

function clearCookieHeader(): string {
  return `${COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict`;
}

// ---------------------------------------------------------------------------
// Safe redirect target validation (prevent open-redirect attacks)
// ---------------------------------------------------------------------------

function safeNext(raw: string | null): string {
  if (!raw) return "/";
  // Only allow same-origin paths starting with "/" and not "//" (which would
  // be treated as protocol-relative by some clients).
  if (!raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

// ---------------------------------------------------------------------------
// Login page (HTML)
// ---------------------------------------------------------------------------

function htmlEscape(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderLogin(opts: { error?: string; next: string }): Response {
  const errorBlock = opts.error
    ? `<div class="error">${htmlEscape(opts.error)}</div>`
    : "";
  const nextHidden = `<input type="hidden" name="next" value="${htmlEscape(opts.next)}">`;

  const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>Sign in · Knowledge Vault</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --bg-1: #0b0d0f;
    --bg-2: #161b22;
    --card: #ffffff;
    --card-fg: #1c2128;
    --muted: #6b7280;
    --border: #e5e7eb;
    --border-strong: #cbd5e1;
    --violet: #4285f4;
    --violet-hover: #1a73e8;
    --magenta: #60a5fa;
    --terminal-green: #22c55e;
    --error-bg: #fef2f2;
    --error-border: #fecaca;
    --error-fg: #b91c1c;
  }
  * { box-sizing: border-box; font-weight: 400 !important; }
  html, body {
    margin: 0;
    padding: 0;
    min-height: 100vh;
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: radial-gradient(ellipse at top, #0c1d3a 0%, var(--bg-1) 50%, var(--bg-2) 100%);
    color: var(--card-fg);
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  body {
    background-image:
      radial-gradient(rgba(66, 133, 244, 0.06) 1px, transparent 1px),
      radial-gradient(ellipse at top, rgba(66, 133, 244, 0.15) 0%, transparent 60%);
    background-size: 24px 24px, 100% 100%;
  }
  .wrap {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .card {
    width: 100%;
    max-width: 420px;
    background: var(--card);
    border-radius: 16px;
    padding: 48px 40px 40px;
    box-shadow:
      0 0 0 1px rgba(66, 133, 244, 0.20),
      0 24px 60px -16px rgba(0, 0, 0, 0.6),
      0 0 80px -20px rgba(66, 133, 244, 0.35);
    position: relative;
  }
  .card::before {
    content: "";
    position: absolute;
    top: 0; left: 24px; right: 24px;
    height: 2px;
    background: linear-gradient(90deg, transparent, var(--violet), var(--magenta), transparent);
    border-radius: 2px;
  }
  /* Optional hero brand image. Drop your image at
     site/quartz/static/brand.jpg, then uncomment this block + the <div class="hero">
     below in the body markup, and add /static/brand.jpg to the public-asset
     allowlist in onRequest() so it isn't auth-gated. */
  /*
  .hero {
    display: block;
    width: 100%;
    aspect-ratio: 1 / 1;
    border-radius: 12px;
    margin: 0 0 22px 0;
    background-color: #0b0d0f;
    background-image: url("/static/brand.jpg");
    background-size: cover;
    background-position: center;
  }
  */
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    font-family: "JetBrains Mono", ui-monospace, monospace;
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--violet);
    margin-bottom: 4px;
  }
  .brand::before {
    content: "";
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--terminal-green);
    box-shadow: 0 0 6px var(--terminal-green);
  }
  h1 {
    font-size: 28px;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin: 0 0 6px;
    color: var(--card-fg);
  }
  .subtitle {
    font-size: 14px;
    color: var(--muted);
    margin: 0 0 32px;
  }
  form {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  label {
    font-size: 12px;
    font-weight: 600;
    color: var(--card-fg);
    letter-spacing: 0.02em;
    text-transform: uppercase;
    font-family: "JetBrains Mono", ui-monospace, monospace;
  }
  input[type="text"],
  input[type="password"] {
    height: 58px;
    padding: 0 16px;
    font-size: 15px;
    font-family: "Inter", sans-serif;
    color: var(--card-fg);
    background: #fafbfc;
    border: 1.5px solid var(--border);
    border-radius: 10px;
    outline: none;
    transition: border-color 120ms ease, box-shadow 120ms ease, background 120ms ease;
    -webkit-appearance: none;
    appearance: none;
  }
  input[type="text"]:hover,
  input[type="password"]:hover {
    border-color: var(--border-strong);
  }
  input[type="text"]:focus,
  input[type="password"]:focus {
    border-color: var(--violet);
    background: #ffffff;
    box-shadow: 0 0 0 4px rgba(66, 133, 244, 0.12);
  }
  button {
    margin-top: 8px;
    height: 58px;
    border: 0;
    border-radius: 10px;
    background: var(--violet);
    color: #ffffff;
    font-size: 15px;
    font-weight: 600;
    font-family: "Inter", sans-serif;
    letter-spacing: 0.01em;
    cursor: pointer;
    transition: background 120ms ease, box-shadow 120ms ease, transform 80ms ease;
  }
  button:hover {
    background: var(--violet-hover);
    box-shadow: 0 8px 24px -8px rgba(66, 133, 244, 0.6);
  }
  button:active {
    transform: translateY(1px);
  }
  .error {
    background: var(--error-bg);
    border: 1px solid var(--error-border);
    color: var(--error-fg);
    padding: 12px 14px;
    border-radius: 8px;
    font-size: 13px;
    margin-bottom: 8px;
  }
  .footer {
    margin-top: 28px;
    padding-top: 20px;
    border-top: 1px solid var(--border);
    font-family: "JetBrains Mono", ui-monospace, monospace;
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.04em;
    text-align: center;
  }
  .footer a { color: var(--violet); text-decoration: none; }
  .footer a:hover { text-decoration: underline; }
  @media (max-width: 480px) {
    .card { padding: 36px 24px 32px; border-radius: 14px; }
    h1 { font-size: 24px; }
  }
</style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <!-- Uncomment after dropping your image at /static/brand.jpg + whitelisting it in onRequest(). -->
      <!-- <div class="hero" role="img" aria-label="Knowledge Vault"></div> -->
      <div class="brand">Knowledge Vault</div>
      <h1>Sign in</h1>
      <p class="subtitle">Enter the shared credentials to view the channel knowledge graph.</p>
      ${errorBlock}
      <form method="POST" action="/login" autocomplete="on">
        ${nextHidden}
        <div class="field">
          <label for="username">Username</label>
          <input type="text" id="username" name="username" required autocomplete="username" autocapitalize="off" autocorrect="off" spellcheck="false" autofocus>
        </div>
        <div class="field">
          <label for="password">Password</label>
          <input type="password" id="password" name="password" required autocomplete="current-password">
        </div>
        <button type="submit">Sign in →</button>
      </form>
      <div class="footer">
        Cyberpunk knowledge vault · auto-built nightly
      </div>
    </div>
  </div>
</body>
</html>`;

  return new Response(html, {
    status: 200,
    headers: {
      "Content-Type": "text/html; charset=UTF-8",
      "Cache-Control": "no-store",
    },
  });
}

// ---------------------------------------------------------------------------
// Main handler
// ---------------------------------------------------------------------------

export const onRequest: PagesFunction<Env> = async ({ request, next, env }) => {
  const expectedUser = (env.SITE_USERNAME ?? "ryder").trim();
  const expectedPass = (env.SITE_PASSWORD ?? "").trim();

  if (!expectedPass) {
    return new Response("Site auth misconfigured: SITE_PASSWORD env var is missing.", {
      status: 503,
      headers: { "Content-Type": "text/plain" },
    });
  }

  const url = new URL(request.url);
  const path = url.pathname;

  // Public-asset allowlist. Anything the login page itself needs to render
  // must bypass the auth gate, otherwise the styled login screen ends up
  // requesting its own background image and getting a 302 → broken render.
  // Keep this list tight — everything else stays gated.
  // If you uncomment the .hero block above and add a brand image at
  // /static/brand.jpg, also add it to this list.
  if (path === "/favicon.ico") {
    return next();
  }

  // Logout helper: any GET to /logout clears the cookie and redirects to /login
  if (path === "/logout") {
    return new Response(null, {
      status: 302,
      headers: { Location: "/login", "Set-Cookie": clearCookieHeader() },
    });
  }

  // POST /login → validate and set cookie
  if (path === "/login" && request.method === "POST") {
    const form = await request.formData();
    const user = (form.get("username") || "").toString().trim();
    const pass = (form.get("password") || "").toString().trim();
    const next_ = safeNext((form.get("next") || "/").toString());

    if (!timingSafeEqual(user, expectedUser) || !timingSafeEqual(pass, expectedPass)) {
      return renderLogin({ error: "Invalid username or password.", next: next_ });
    }

    const cookieVal = await makeCookieValue(expectedUser, expectedPass);
    return new Response(null, {
      status: 302,
      headers: { Location: next_, "Set-Cookie": setCookieHeader(cookieVal) },
    });
  }

  // GET /login → show form
  if (path === "/login" && request.method === "GET") {
    return renderLogin({ next: safeNext(url.searchParams.get("next")) });
  }

  // Any other route → check cookie
  const cookie = getCookie(request, COOKIE_NAME);
  if (cookie) {
    const valid = await verifyCookieValue(cookie, expectedUser, expectedPass);
    if (valid) return next();
  }

  // Missing or invalid cookie → redirect to login, preserving target
  const nextParam = encodeURIComponent(path + url.search);
  return new Response(null, {
    status: 302,
    headers: { Location: `/login?next=${nextParam}`, "Set-Cookie": clearCookieHeader() },
  });
};
