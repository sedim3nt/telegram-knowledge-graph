// Ask Bridg3 — chat panel client. Stateless server; history lives in localStorage.

const HISTORY_KEY = "clr-ask-history"
const HISTORY_MAX_TURNS = 6 // each turn = user + assistant pair
const ASK_ENDPOINT = "/api/ask"

interface Turn {
  role: "user" | "assistant"
  content: string
}

function loadHistory(): Turn[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((t: any) => t && (t.role === "user" || t.role === "assistant") && typeof t.content === "string")
      .slice(-HISTORY_MAX_TURNS * 2)
  } catch {
    return []
  }
}

function saveHistory(turns: Turn[]) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(turns.slice(-HISTORY_MAX_TURNS * 2)))
  } catch {
    /* localStorage full or disabled — fail silently */
  }
}

function clearHistoryStorage() {
  try {
    localStorage.removeItem(HISTORY_KEY)
  } catch {
    /* noop */
  }
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
}

// Minimal markdown → HTML. Bridg3 emits short markdown answers so we don't
// need a full library; this covers links, bold/italic, code, lists, paragraphs.
function renderMarkdown(s: string): string {
  let out = escapeHtml(s)
  out = out.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${code}</code></pre>`)
  out = out.replace(/`([^`\n]+)`/g, "<code>$1</code>")
  out = out.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
  out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, text, url) => {
    const safe = url.replace(/"/g, "&quot;")
    const isExternal = /^https?:\/\//i.test(url)
    const attrs = isExternal ? ' target="_blank" rel="noopener noreferrer"' : ""
    return `<a href="${safe}"${attrs}>${text}</a>`
  })
  out = out.replace(/(?:^|\n)((?:[-*] .+(?:\n|$))+)/g, (_, block) => {
    const items = block
      .trim()
      .split(/\n/)
      .map((l: string) => l.replace(/^[-*]\s+/, "").trim())
    return "\n<ul>" + items.map((it: string) => `<li>${it}</li>`).join("") + "</ul>"
  })
  out = out
    .split(/\n\n+/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) =>
      p.startsWith("<ul>") || p.startsWith("<pre>") || p.startsWith("<p>")
        ? p
        : `<p>${p.replace(/\n/g, "<br>")}</p>`,
    )
    .join("")
  return out
}

function setupAsk() {
  const launcher = document.getElementById("askbridg3-launcher") as HTMLButtonElement | null
  const drawer = document.getElementById("askbridg3-drawer") as HTMLDivElement | null
  if (!launcher || !drawer) return

  const closeBtn = drawer.querySelector(".askbridg3-close") as HTMLButtonElement | null
  const form = document.getElementById("askbridg3-form") as HTMLFormElement | null
  const input = document.getElementById("askbridg3-input") as HTMLTextAreaElement | null
  const sendBtn = document.getElementById("askbridg3-send") as HTMLButtonElement | null
  const messages = document.getElementById("askbridg3-messages") as HTMLDivElement | null
  const scopePanel = document.getElementById("askbridg3-scope") as HTMLDivElement | null
  const scopeName = document.getElementById("askbridg3-scope-name") as HTMLSpanElement | null
  const scopeCheckbox = document.getElementById("askbridg3-scope-checkbox") as HTMLInputElement | null
  const contextLine = document.getElementById("askbridg3-context-line") as HTMLDivElement | null
  const clearBtn = document.getElementById("askbridg3-clear") as HTMLButtonElement | null

  if (!form || !input || !sendBtn || !messages) return

  const slug = launcher.dataset.pageSlug || ""
  const title = launcher.dataset.pageTitle || ""
  const isPageScoped = slug.startsWith("concepts/") || slug.startsWith("people/")

  if (isPageScoped && scopePanel && scopeName) {
    scopePanel.hidden = false
    scopeName.textContent = title || slug
    if (contextLine) contextLine.textContent = `Asking about ${title || slug}`
  } else if (contextLine) {
    contextLine.textContent = "Channel-wide"
  }

  const renderHistoryToDom = () => {
    const greeting = messages.querySelector(".askbridg3-greeting")
    messages.innerHTML = ""
    if (greeting) messages.appendChild(greeting)
    for (const t of loadHistory()) {
      appendMessage(t.role, renderMarkdown(t.content))
    }
  }

  const open = () => {
    drawer.hidden = false
    document.body.classList.add("askbridg3-open")
    renderHistoryToDom()
    queueMicrotask(() => input.focus())
  }
  const close = () => {
    drawer.hidden = true
    document.body.classList.remove("askbridg3-open")
  }

  launcher.addEventListener("click", open)
  closeBtn?.addEventListener("click", close)
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !drawer.hidden) close()
  })

  function appendMessage(
    role: "user" | "assistant" | "error" | "thinking",
    html: string,
  ): HTMLDivElement {
    const div = document.createElement("div")
    div.className = `askbridg3-msg askbridg3-msg-${role}`
    div.innerHTML = html
    messages!.appendChild(div)
    messages!.scrollTop = messages!.scrollHeight
    return div
  }

  async function ask(question: string) {
    const turns = loadHistory()
    const useScope = isPageScoped && scopeCheckbox?.checked
    const current_page = useScope ? `${title || slug} (/${slug})` : null

    appendMessage("user", renderMarkdown(question))
    const thinking = appendMessage(
      "thinking",
      '<span class="askbridg3-thinking-dots"><span></span><span></span><span></span></span>',
    )

    try {
      const resp = await fetch(ASK_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          history: turns,
          current_page,
        }),
      })
      thinking.remove()
      if (!resp.ok) {
        let errBody: any = null
        try {
          errBody = await resp.json()
        } catch {
          /* not json */
        }
        // 503 + quota_exhausted is the friendly "Bridg3 is resting" path —
        // render the server-supplied human message directly, no error chrome.
        if (resp.status === 503 && errBody?.error === "quota_exhausted" && errBody?.message) {
          appendMessage("assistant", renderMarkdown(String(errBody.message)))
          return
        }
        const detail =
          errBody?.error ||
          errBody?.detail ||
          (errBody?.message ? String(errBody.message) : `${resp.status}`)
        appendMessage(
          "error",
          `<p>Couldn't reach Bridg3. <code>${escapeHtml(String(detail).slice(0, 200))}</code></p>`,
        )
        return
      }
      const data = await resp.json()
      const answer = (data.answer as string) || ""
      appendMessage("assistant", renderMarkdown(answer))
      const newTurns: Turn[] = [
        ...turns,
        { role: "user", content: question },
        { role: "assistant", content: answer },
      ]
      saveHistory(newTurns)
    } catch (e) {
      thinking.remove()
      appendMessage("error", `<p>Network error: ${escapeHtml(String(e))}</p>`)
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault()
    const q = input.value.trim()
    if (!q) return
    input.value = ""
    input.style.height = "auto"
    sendBtn.disabled = true
    ask(q).finally(() => {
      sendBtn.disabled = false
      input.focus()
    })
  })

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      form.requestSubmit()
    }
  })

  input.addEventListener("input", () => {
    input.style.height = "auto"
    input.style.height = Math.min(input.scrollHeight, 160) + "px"
  })

  clearBtn?.addEventListener("click", () => {
    clearHistoryStorage()
    renderHistoryToDom()
    input.focus()
  })
}

document.addEventListener("nav", setupAsk)
setupAsk()
