/**
 * The artifact's own page in an iframe; `version` changes make it reload (the query string busts nothing but the
 * document, relative media keep their URLs) at `page`, so a rebuild does not throw the user back to the start.
 */

export function Preview({ artifactId, version, page }: { artifactId: string; version: number; page: number | null }) {
  return (
    <iframe
      key={version}
      src={`/artifacts/${artifactId}/?v=${version}${page ? `#${page}` : ''}`}
      title="Artifact preview"
      className="h-full w-full border-0 bg-bg"
    />
  )
}
