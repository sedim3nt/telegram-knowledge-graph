import { QuartzConfig } from "./quartz/cfg"
import * as Plugin from "./quartz/plugins"

/**
 * Knowledge Vault — Quartz config
 *
 * Cyberpunk dark theme. Content is symlinked to ../vault.
 * Auth gating happens at the edge via functions/_middleware.ts (Cloudflare Pages).
 *
 * Customize the three commented fields below for your channel before deploy.
 */
const config: QuartzConfig = {
  configuration: {
    // Customize for your channel:
    pageTitle: "Knowledge Vault",
    pageTitleSuffix: "",
    enableSPA: true,
    enablePopovers: true,
    analytics: null,
    locale: "en-US",
    baseUrl: "your-site.example.com",
    ignorePatterns: ["_atomic", "_meta", "private", "templates", ".obsidian"],
    defaultDateType: "modified",
    generateSocialImages: false,
    theme: {
      fontOrigin: "googleFonts",
      cdnCaching: true,
      typography: {
        // Geist isn't on Google Fonts; Inter is the closest GA grotesque.
        // We'll override with Geist via fontsource if we want pixel-exact later.
        header: "Inter",
        body: "Inter",
        code: "JetBrains Mono",
      },
      colors: {
        // Light mode kept livable, but the site defaults to dark — Railway palette.
        lightMode: {
          light: "#fafafa",
          lightgray: "#e6edf3",
          gray: "#7d8590",
          darkgray: "#30363d",
          dark: "#0b0d0f",
          secondary: "#4285f4",      // Google medium blue
          tertiary: "#22c55e",        // Terminal green
          highlight: "rgba(66, 133, 244, 0.10)",
          textHighlight: "rgba(96, 165, 250, 0.30)",
        },
        darkMode: {
          // Cyberpunk base, blue accents
          light: "#0b0d0f",           // bg primary
          lightgray: "#161b22",       // surfaces / cards
          gray: "#7d8590",            // muted text
          darkgray: "#e6edf3",        // primary text (warm off-white)
          dark: "#ffffff",            // strong text
          secondary: "#4285f4",       // accent medium blue
          tertiary: "#22c55e",        // terminal green
          highlight: "rgba(66, 133, 244, 0.18)",
          textHighlight: "rgba(96, 165, 250, 0.35)",
        },
      },
    },
  },
  plugins: {
    transformers: [
      Plugin.FrontMatter(),
      Plugin.CreatedModifiedDate({
        priority: ["frontmatter", "git", "filesystem"],
      }),
      Plugin.SyntaxHighlighting({
        theme: {
          light: "github-light",
          dark: "github-dark",
        },
        keepBackground: false,
      }),
      Plugin.ObsidianFlavoredMarkdown({ enableInHtmlEmbed: false }),
      Plugin.GitHubFlavoredMarkdown(),
      Plugin.TableOfContents(),
      Plugin.CrawlLinks({ markdownLinkResolution: "shortest" }),
      Plugin.Description(),
      Plugin.Latex({ renderEngine: "katex" }),
    ],
    filters: [Plugin.RemoveDrafts()],
    emitters: [
      Plugin.AliasRedirects(),
      Plugin.ComponentResources(),
      Plugin.ContentPage(),
      Plugin.FolderPage(),
      Plugin.TagPage(),
      Plugin.ContentIndex({
        enableSiteMap: true,
        enableRSS: true,
      }),
      Plugin.Assets(),
      Plugin.Static(),
      Plugin.Favicon(),
      Plugin.NotFoundPage(),
      // CustomOgImages adds significant build time; skip for now
    ],
  },
}

export default config
