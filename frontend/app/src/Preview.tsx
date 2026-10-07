/** The artifact's own page in an iframe; `version` changes make it reload (the query string busts nothing but the
 * document, relative media keep their URLs). */

export function Preview({ artifactId, version }: { artifactId: string; version: number }) {
  return (
    <iframe
      key={version}
      src={`/artifacts/${artifactId}/?v=${version}`}
      title="Artifact preview"
      className="h-full w-full border-0 bg-bg"
    />
  )
}
