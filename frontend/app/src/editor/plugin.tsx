/**
 * The MDXEditor plugin that makes it safe on OpenArtifact markdown.
 *
 * MDXEditor parses MDX by default, which drops HTML comments (the page directives) and turns `<component>` tags
 * into JSX it does not know. The editor therefore runs with `suppressHtmlProcessing`, so the markdown parses as
 * plain CommonMark and every piece of raw HTML (a directive comment, a component tag, a `<div data-step>`) is an
 * mdast `html` node. This plugin keeps those verbatim as `RawHtmlNode`s, editable as text, and tells the `---`
 * page break apart from a `***` rule (both are a thematic break to CommonMark) with `PageBreakNode`, so a page
 * break stays `---` on export and a rule inside a page is written as `***`.
 */

import {
  addExportVisitor$,
  addImportVisitor$,
  addLexicalNode$,
  addMdastExtension$,
  addToMarkdownExtension$,
  ButtonWithTooltip,
  insertDecoratorNode$,
  type LexicalExportVisitor,
  type MdastExtension,
  type MdastImportVisitor,
  realmPlugin,
  usePublisher,
} from '@mdxeditor/editor'
import { $getNodeByKey, DecoratorNode, type LexicalEditor, type NodeKey, type SerializedLexicalNode } from 'lexical'
import { SeparatorHorizontal } from 'lucide-react'
import type * as Mdast from 'mdast'
import { type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from 'react'

/** A thematic break with the text it was written as, recorded by `breakMarker` while parsing. */
interface MarkedThematicBreak extends Mdast.ThematicBreak {
  marker?: string
}

/** The one line that ends a page, in every artifact type. */
const PAGE_BREAK = '---'

/** mdast parents whose `html` children are blocks; under anything else (a paragraph, a heading) HTML is inline. */
const BLOCK_PARENTS = new Set(['root', 'blockquote', 'listItem'])

// ---------------------------------------------------------------------------
// Raw HTML
// ---------------------------------------------------------------------------

export type SerializedRawHtmlNode = SerializedLexicalNode & { html: string; inline: boolean }

/** A run of raw HTML kept exactly as written: a directive comment, a component tag, an inline `<span>`. */
export class RawHtmlNode extends DecoratorNode<ReactNode> {
  __html: string
  __inline: boolean

  static getType(): string {
    return 'oa-raw-html'
  }

  static clone(node: RawHtmlNode): RawHtmlNode {
    return new RawHtmlNode(node.__html, node.__inline, node.__key)
  }

  static importJSON(json: SerializedRawHtmlNode): RawHtmlNode {
    return $createRawHtmlNode(json.html, json.inline)
  }

  constructor(html: string, inline: boolean, key?: NodeKey) {
    super(key)
    this.__html = html
    this.__inline = inline
  }

  exportJSON(): SerializedRawHtmlNode {
    return { ...super.exportJSON(), html: this.__html, inline: this.__inline }
  }

  createDOM(): HTMLElement {
    const element = document.createElement(this.__inline ? 'span' : 'div')
    element.className = this.__inline ? 'oa-raw-html oa-raw-html-inline' : 'oa-raw-html oa-raw-html-block'
    return element
  }

  updateDOM(): boolean {
    return false
  }

  isInline(): boolean {
    return this.__inline
  }

  isKeyboardSelectable(): boolean {
    return true
  }

  getHtml(): string {
    return this.getLatest().__html
  }

  setHtml(html: string): void {
    this.getWritable().__html = html
  }

  decorate(editor: LexicalEditor): ReactNode {
    return <RawHtml editor={editor} nodeKey={this.__key} html={this.__html} inline={this.__inline} />
  }
}

export function $createRawHtmlNode(html: string, inline: boolean): RawHtmlNode {
  return new RawHtmlNode(html, inline)
}

/** The raw HTML as monospace text; a click turns it into a textarea, and leaving it writes the text back. */
function RawHtml({
  editor,
  nodeKey,
  html,
  inline,
}: {
  editor: LexicalEditor
  nodeKey: NodeKey
  html: string
  inline: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(html)
  const area = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    if (editing) area.current?.focus()
  }, [editing])
  const commit = () => {
    setEditing(false)
    if (draft === html) return
    editor.update(() => {
      const node = $getNodeByKey(nodeKey)
      if (node instanceof RawHtmlNode) node.setHtml(draft)
    })
  }
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Escape') {
      setDraft(html)
      setEditing(false)
    } else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      commit()
    }
    // Lexical must not see the keystrokes of a textarea inside its tree.
    e.stopPropagation()
  }
  if (editing) {
    return (
      <textarea
        ref={area}
        className="oa-raw-html-input"
        value={draft}
        rows={inline ? 1 : Math.max(2, draft.split('\n').length)}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={onKey}
        spellCheck={false}
      />
    )
  }
  return (
    <button
      type="button"
      className="oa-raw-html-text"
      title="HTML, kept as written; click to edit"
      onClick={() => {
        setDraft(html)
        setEditing(true)
      }}
    >
      {html}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Page breaks
// ---------------------------------------------------------------------------

/** The `---` between two pages, shown as a labelled divider. */
export class PageBreakNode extends DecoratorNode<ReactNode> {
  static getType(): string {
    return 'oa-page-break'
  }

  static clone(node: PageBreakNode): PageBreakNode {
    return new PageBreakNode(node.__key)
  }

  static importJSON(): PageBreakNode {
    return $createPageBreakNode()
  }

  createDOM(): HTMLElement {
    const element = document.createElement('div')
    element.className = 'oa-page-break'
    return element
  }

  updateDOM(): boolean {
    return false
  }

  isInline(): boolean {
    return false
  }

  isKeyboardSelectable(): boolean {
    return true
  }

  decorate(): ReactNode {
    return <span className="oa-page-break-label">page break</span>
  }
}

export function $createPageBreakNode(): PageBreakNode {
  return new PageBreakNode()
}

/** A toolbar button inserting a page break at the cursor. */
export function InsertPageBreak() {
  const insert = usePublisher(insertDecoratorNode$)
  return (
    <ButtonWithTooltip title="Insert page break" onClick={() => insert(() => $createPageBreakNode())}>
      <SeparatorHorizontal size={18} />
    </ButtonWithTooltip>
  )
}

// ---------------------------------------------------------------------------
// Parsing and serialising
// ---------------------------------------------------------------------------

/**
 * Records on each thematic break the text it was written as. mdast forgets the marker, and `---` is a page break
 * where `***` is a rule, so the import visitor needs it. The handler replaces mdast's default exit for the token,
 * which only closes the node, so it closes it too.
 */
const breakMarker: MdastExtension = {
  exit: {
    thematicBreak(token) {
      const node = this.stack[this.stack.length - 1] as MarkedThematicBreak
      node.marker = this.sliceSerialize(token).trim()
      this.exit(token)
    },
  },
}

const importRawHtml: MdastImportVisitor<Mdast.Html> = {
  testNode: 'html',
  visitNode({ mdastNode, mdastParent, actions }) {
    const inline = mdastParent !== null && !BLOCK_PARENTS.has(mdastParent.type)
    actions.addAndStepInto($createRawHtmlNode(mdastNode.value, inline))
  },
}

// Registered before the thematic break plugin's visitor, which takes every break left over.
const importPageBreak: MdastImportVisitor<MarkedThematicBreak> = {
  testNode: (node) => node.type === 'thematicBreak' && (node as MarkedThematicBreak).marker === PAGE_BREAK,
  visitNode({ actions }) {
    actions.addAndStepInto($createPageBreakNode())
  },
}

const exportRawHtml: LexicalExportVisitor<RawHtmlNode, Mdast.Html> = {
  testLexicalNode: (node): node is RawHtmlNode => node instanceof RawHtmlNode,
  visitLexicalNode({ lexicalNode, actions }) {
    actions.addAndStepInto('html', { value: lexicalNode.getHtml() }, false)
  },
}

const exportPageBreak: LexicalExportVisitor<PageBreakNode, Mdast.Nodes> = {
  testLexicalNode: (node): node is PageBreakNode => node instanceof PageBreakNode,
  visitLexicalNode({ actions }) {
    actions.addAndStepInto('pageBreak', {}, false)
  },
}

/** The plugin: list it before `thematicBreakPlugin()` so page breaks are claimed first. */
export const openArtifactPlugin = realmPlugin({
  init(realm) {
    realm.pubIn({
      [addLexicalNode$]: [RawHtmlNode, PageBreakNode],
      [addMdastExtension$]: breakMarker,
      [addImportVisitor$]: [importRawHtml, importPageBreak],
      [addExportVisitor$]: [exportRawHtml, exportPageBreak],
      // `pageBreak` is our own mdast type; mdast-util-to-markdown writes it through this handler.
      [addToMarkdownExtension$]: { handlers: { pageBreak: () => PAGE_BREAK } },
    })
  },
})
