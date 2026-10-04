/**
 * Shapes shared between `build.py` (which writes the JSON blob into the page)
 * and the browser runtime (which reads it). Keep in sync with `render_page` in build.py.
 */

/** A single tab entry shown in the slide topbar tab navigation. */
export interface DeckTab {
  id: string
  label: string
}

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
  /** Browser tab title; also the fallback when a slide has no title or h1. */
  title?: string
  theme: DeckTheme
  /** Optional footer text rendered bottom-right of every slide. */
  footer?: string
  /** Tabs for the topbar nav bar; `<slide tab="...">` highlights the matching one. Decks only. */
  tabs: DeckTab[]
}

/** The JSON blob embedded in `<script type="application/json" id="artifact-data">`. */
export interface ArtifactData {
  config: ArtifactConfig
  /** Raw `deck.md` source. For a deck it is split into slides in the browser; otherwise rendered whole. */
  markdown: string
  /** `<component src="X">` -> file contents, keyed by the `src` attribute. */
  components: Record<string, string>
  /** The user's `styles.css` (or an empty string). */
  styles: string
}
