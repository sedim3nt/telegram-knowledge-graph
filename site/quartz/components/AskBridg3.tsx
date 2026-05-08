import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import style from "./styles/askbridg3.scss"
// @ts-ignore
import script from "./scripts/askbridg3.inline"

const AskBridg3: QuartzComponent = ({ fileData }: QuartzComponentProps) => {
  const slug = fileData.slug ?? ""
  const title = (fileData.frontmatter?.title as string | undefined) ?? ""
  return (
    <>
      <button
        id="askbridg3-launcher"
        aria-label="Ask Bridg3"
        data-page-slug={slug}
        data-page-title={title}
      >
        <span class="askbridg3-icon" aria-hidden="true">🐯</span>
        <span class="askbridg3-tooltip">Ask Bridg3</span>
      </button>
      <div id="askbridg3-drawer" hidden>
        <div class="askbridg3-drawer-content">
          <header class="askbridg3-header">
            <div class="askbridg3-brand">
              <span class="askbridg3-mark" aria-hidden="true">🐯</span>
              <div>
                <div class="askbridg3-title">Ask Bridg3</div>
                <div class="askbridg3-subtitle" id="askbridg3-context-line">
                  Channel-wide
                </div>
              </div>
            </div>
            <button class="askbridg3-close" aria-label="Close panel">✕</button>
          </header>
          <div class="askbridg3-scope" id="askbridg3-scope" hidden>
            <label class="askbridg3-scope-toggle">
              <input type="checkbox" id="askbridg3-scope-checkbox" checked />
              <span>
                Asking about <strong id="askbridg3-scope-name">this page</strong>
              </span>
            </label>
          </div>
          <div class="askbridg3-messages" id="askbridg3-messages">
            <div class="askbridg3-greeting">
              Hey — I've read every message in the channel. Ask me what people
              think about a tool, who's pushed back on a take, or what's
              changed lately. <span class="askbridg3-mark">🐯</span>
            </div>
          </div>
          <form class="askbridg3-form" id="askbridg3-form">
            <textarea
              id="askbridg3-input"
              placeholder="What did the channel decide about LangGraph vs CrewAI?"
              rows={2}
              autocomplete="off"
            />
            <button type="submit" id="askbridg3-send" aria-label="Send question">→</button>
          </form>
          <footer class="askbridg3-footer">
            <button type="button" id="askbridg3-clear" class="askbridg3-link">
              Clear chat
            </button>
            <span class="askbridg3-disclaimer">Vault-only · may be wrong</span>
          </footer>
        </div>
      </div>
    </>
  )
}

AskBridg3.afterDOMLoaded = script
AskBridg3.css = style

export default (() => AskBridg3) satisfies QuartzComponentConstructor
