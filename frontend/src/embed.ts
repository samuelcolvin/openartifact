/**
 * What the page leaves on `window` for whoever embeds it in a frame (the editor's live preview): the artifact's
 * type and title, and for a deck its navigation controller. `main.ts` sets it once the page is built.
 */

import type { DeckController } from './deck.ts'
import type { ArtifactType } from './types.ts'

export interface OpenArtifactHandle {
  type: ArtifactType
  title?: string
  /** The deck's navigation, `null` for a document or page artifact. */
  deck: DeckController | null
}

declare global {
  interface Window {
    openartifact?: OpenArtifactHandle
  }
}
