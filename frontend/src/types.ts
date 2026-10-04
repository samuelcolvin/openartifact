/**
 * Shapes shared between `build.py` (which writes the data blocks into the page)
 * and the browser runtime (which reads them). Keep in sync with `render_page` in build.py.
 */

/**
 * Built-in deck theme. `light` / `dark` pick the palette; the `markdown-*`
 * variants add source-style decorations (heading `#` prefixes, `**` markers,
 * traffic-light dots, mono slide counter, diamond bullets).
 */
export type DeckTheme = 'light' | 'dark' | 'markdown-light' | 'markdown-dark'

/**
 * The overall form of the artifact. `deck` is slides with navigation, one per page in
 * print; `document` is a fixed-width sheet that prints to pages like a word processor;
 * `page` is a continuous, fluid page like a Notion page.
 */
export type ArtifactType = 'deck' | 'document' | 'page'

/** Artifact-level config, parsed from `artifact.toml` by build.py. */
export interface ArtifactConfig {
  type: ArtifactType
  /** Browser tab title; also the fallback when a page has no title or h1. */
  title?: string
  theme: DeckTheme
  /** A component rendered once per page around the body (`{{ CONTENT }}`); keyed like `components`. */
  page_component?: string
  /** The `[context]` table of artifact.toml: uppercase keys substituted as `{{ KEY }}` everywhere. */
  context?: Record<string, string>
}

/**
 * The artifact as read from the page's data blocks: `#artifact-config` (JSON), `#artifact-markdown`
 * (`<script type="text/markdown">`) and one `<script type="text/html" data-component="X">` per component.
 * The user's `styles.css` is not here: it is a live `<style id="artifact-styles">` in the head.
 */
export interface ArtifactData {
  config: ArtifactConfig
  /** Raw `main.md` source. For a deck it is split into slides in the browser; otherwise rendered whole. */
  markdown: string
  /** `<component src="X">` -> file contents, keyed by the `src` attribute. */
  components: Record<string, string>
}
