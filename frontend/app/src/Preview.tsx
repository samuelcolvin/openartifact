/**
 * The artifact's own page in an iframe; `version` changes make it reload (the query string busts nothing but the
 * document, relative media keep their URLs) at `page`, so a rebuild does not throw the user back to the start.
 * Once the page is up, `onDeck` gets its navigation controller, the handle the runtime leaves on the frame's window
 * (`null` for a document or page artifact).
 */

import type { DeckController } from '../../src/deck.ts'
import type {} from '../../src/embed.ts'

export function Preview({
  artifactId,
  version,
  page,
  onDeck,
}: {
  artifactId: string
  version: number
  page: number | null
  onDeck?: (deck: DeckController | null) => void
}) {
  return (
    <iframe
      key={version}
      src={`/artifacts/${artifactId}/?v=${version}${page ? `#${page}` : ''}`}
      title="Artifact preview"
      className="h-full w-full border-0 bg-bg"
      onLoad={(e) => onDeck?.(e.currentTarget.contentWindow?.openartifact?.deck ?? null)}
    />
  )
}
