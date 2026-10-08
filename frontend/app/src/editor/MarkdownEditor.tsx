/**
 * The markdown editor beside the chat: MDXEditor in rich mode (a WYSIWYG view of the page's markdown) or raw mode
 * (its CodeMirror source editor), with one toolbar over both. Raw mode is MDXEditor's own source view, so the
 * formatting buttons act on the rich document only; raw mode greys them out.
 *
 * The markdown is parsed as CommonMark, not MDX (`suppressHtmlProcessing`), and `openArtifactPlugin` keeps the
 * HTML, directive comments and page breaks the builder relies on; see `plugin.tsx`. Images referenced relatively
 * are previewed from the artifact's own URL.
 */

import {
  BlockTypeSelect,
  BoldItalicUnderlineToggles,
  CodeToggle,
  CreateLink,
  codeBlockPlugin,
  codeMirrorPlugin,
  diffSourcePlugin,
  headingsPlugin,
  InsertCodeBlock,
  InsertImage,
  InsertTable,
  InsertThematicBreak,
  imagePlugin,
  ListsToggle,
  linkDialogPlugin,
  linkPlugin,
  listsPlugin,
  MDXEditor,
  markdownShortcutPlugin,
  quotePlugin,
  Separator,
  tablePlugin,
  thematicBreakPlugin,
  toolbarPlugin,
  UndoRedo,
  usePublisher,
  viewMode$,
} from '@mdxeditor/editor'
import '@mdxeditor/editor/style.css'
import { useEffect, useRef } from 'react'
import { InsertPageBreak, openArtifactPlugin } from './plugin.tsx'
import { sourceExtensions } from './sourceTheme.ts'

export type EditMode = 'rich' | 'raw'

/** The languages a fenced block may be set to from the code block's own menu; the runtime highlights these. */
const CODE_LANGUAGES: Record<string, string> = {
  '': 'Plain text',
  bash: 'Bash',
  css: 'CSS',
  diff: 'Diff',
  go: 'Go',
  html: 'HTML',
  ini: 'INI',
  javascript: 'JavaScript',
  js: 'JavaScript (js)',
  json: 'JSON',
  markdown: 'Markdown',
  md: 'Markdown (md)',
  python: 'Python',
  py: 'Python (py)',
  rust: 'Rust',
  sh: 'Shell',
  sql: 'SQL',
  toml: 'TOML',
  typescript: 'TypeScript',
  ts: 'TypeScript (ts)',
  xml: 'XML',
  yaml: 'YAML',
  yml: 'YAML (yml)',
}

/** Drives MDXEditor's view mode from the editor's own toggle; the plugin reads the mode once, at creation. */
function ViewModeSync({ mode }: { mode: EditMode }) {
  const setViewMode = usePublisher(viewMode$)
  useEffect(() => {
    setViewMode(mode === 'raw' ? 'source' : 'rich-text')
  }, [mode, setViewMode])
  return null
}

function Toolbar({ mode }: { mode: EditMode }) {
  const raw = mode === 'raw'
  return (
    <>
      <ViewModeSync mode={mode} />
      <fieldset
        disabled={raw}
        className="contents"
        title={raw ? 'Formatting buttons work on the rich view; raw mode edits the markdown as text' : undefined}
      >
        <UndoRedo />
        <Separator />
        <BoldItalicUnderlineToggles options={['Bold', 'Italic']} />
        <CodeToggle />
        <Separator />
        <BlockTypeSelect />
        <ListsToggle />
        <Separator />
        <CreateLink />
        <InsertImage />
        <InsertTable />
        <InsertCodeBlock />
        <InsertThematicBreak />
        <InsertPageBreak />
      </fieldset>
    </>
  )
}

interface MarkdownEditorProps {
  artifactId: string
  /** The markdown the editor starts from; change the component's `key` to start over from new content. */
  markdown: string
  mode: EditMode
  /** The markdown after an edit, or null when the document is back to what the editor was given. */
  onChange: (markdown: string | null) => void
  /** Rich mode could not parse the markdown: the message, for the owner of the toggle to show and fall back. */
  onParseError: (error: string) => void
}

export function MarkdownEditor({ artifactId, markdown, mode, onChange, onParseError }: MarkdownEditorProps) {
  const base = `/artifacts/${artifactId}/`
  const baseline = useRef<string | null>(null)
  return (
    <MDXEditor
      className="oa-mdx dark-theme"
      contentEditableClassName="oa-prose"
      markdown={markdown}
      onChange={(markdown, initialMarkdownNormalize) => {
        // The first call is MDXEditor's own serialisation of what it parsed (spacing, table padding), not an
        // edit, and later ones may repeat it; an edit is a departure from that baseline, and undoing back to
        // it is clean again.
        if (initialMarkdownNormalize) baseline.current = markdown
        else onChange(markdown === baseline.current ? null : markdown)
      }}
      onError={({ error }) => onParseError(error)}
      suppressHtmlProcessing
      toMarkdownOptions={{ bullet: '-', emphasis: '_', fences: true, listItemIndent: 'one' }}
      plugins={[
        // Before thematicBreakPlugin: page breaks must be claimed before the generic rule visitor sees them.
        openArtifactPlugin(),
        headingsPlugin(),
        listsPlugin(),
        quotePlugin(),
        thematicBreakPlugin(),
        linkPlugin(),
        linkDialogPlugin(),
        imagePlugin({
          // A relative `src` is the artifact's own file; absolute URLs are left alone.
          imagePreviewHandler: async (src) =>
            /^(?:[a-z]+:)?\/\//i.test(src) || src.startsWith('/') ? src : base + src,
          disableImageResize: true,
        }),
        tablePlugin(),
        codeBlockPlugin({ defaultCodeBlockLanguage: '' }),
        codeMirrorPlugin({ codeBlockLanguages: CODE_LANGUAGES, codeMirrorExtensions: sourceExtensions }),
        markdownShortcutPlugin(),
        diffSourcePlugin({
          viewMode: mode === 'raw' ? 'source' : 'rich-text',
          codeMirrorExtensions: sourceExtensions,
        }),
        toolbarPlugin({ toolbarContents: () => <Toolbar mode={mode} /> }),
      ]}
    />
  )
}
