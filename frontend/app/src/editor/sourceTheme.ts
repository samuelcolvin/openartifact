/**
 * The CodeMirror theme for MDXEditor's source (raw) mode, in the app's dark palette. MDXEditor ships a light
 * theme and puts custom extensions ahead of it, so these rules win.
 */

import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import type { Extension } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { tags } from '@lezer/highlight'

const theme = EditorView.theme(
  {
    '&': { backgroundColor: 'var(--color-bg)', color: 'var(--color-fg)', height: '100%' },
    '.cm-content': {
      fontFamily: 'var(--font-mono)',
      fontSize: '14.5px',
      lineHeight: '1.55',
      padding: '12px 0',
      caretColor: 'var(--color-fg)',
    },
    '.cm-scroller': { fontFamily: 'var(--font-mono)' },
    '.cm-gutters': {
      backgroundColor: 'var(--color-bg)',
      color: 'var(--color-faint)',
      borderRight: '1px solid var(--color-line)',
    },
    '.cm-activeLineGutter': { backgroundColor: 'rgba(255, 255, 255, 0.04)', color: 'var(--color-muted)' },
    '.cm-activeLine': { backgroundColor: 'rgba(255, 255, 255, 0.04)' },
    '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--color-fg)' },
    '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, ::selection': {
      backgroundColor: 'rgba(74, 158, 255, 0.3)',
    },
    '.cm-selectionMatch': { backgroundColor: 'rgba(74, 158, 255, 0.18)' },
    '.cm-matchingBracket, &.cm-focused .cm-matchingBracket': {
      backgroundColor: 'rgba(255, 255, 255, 0.1)',
      outline: 'none',
    },
    '.cm-foldPlaceholder': { backgroundColor: 'var(--color-raised)', border: 'none', color: 'var(--color-muted)' },
    '.cm-tooltip': { backgroundColor: 'var(--color-raised)', border: '1px solid var(--color-line)' },
    '.cm-panels': { backgroundColor: 'var(--color-panel)', color: 'var(--color-fg)' },
    '.cm-searchMatch': { backgroundColor: 'rgba(255, 180, 84, 0.3)' },
  },
  { dark: true },
)

const highlight = HighlightStyle.define([
  { tag: tags.heading, color: 'var(--color-accent)', fontWeight: '600' },
  { tag: tags.emphasis, fontStyle: 'italic' },
  { tag: tags.strong, fontWeight: '700' },
  { tag: tags.strikethrough, textDecoration: 'line-through' },
  { tag: tags.link, color: 'var(--color-accent)' },
  { tag: tags.url, color: 'var(--color-ok)' },
  { tag: tags.monospace, color: 'var(--color-warn)' },
  { tag: tags.quote, color: 'var(--color-muted)', fontStyle: 'italic' },
  { tag: tags.list, color: 'var(--color-accent-2)' },
  { tag: tags.contentSeparator, color: 'var(--color-accent-2)', fontWeight: '700' },
  { tag: tags.comment, color: 'var(--color-faint)' },
  { tag: tags.meta, color: 'var(--color-muted)' },
  { tag: tags.processingInstruction, color: 'var(--color-faint)' },
  { tag: tags.tagName, color: 'var(--color-accent-2)' },
  { tag: tags.attributeName, color: 'var(--color-ok)' },
  { tag: tags.attributeValue, color: 'var(--color-warn)' },
  { tag: tags.string, color: 'var(--color-warn)' },
  { tag: tags.keyword, color: 'var(--color-accent-2)' },
  { tag: tags.labelName, color: 'var(--color-ok)' },
])

/** The extensions MDXEditor's source editor is given. */
export const sourceExtensions: Extension[] = [theme, syntaxHighlighting(highlight)]
