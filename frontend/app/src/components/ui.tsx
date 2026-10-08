/** Small building blocks shared by the pages: buttons, badges, form fields, a native dialog, an avatar. */

import { ChevronDown } from 'lucide-react'
import type { ButtonHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react'
import { useEffect, useRef } from 'react'
import type { Visibility } from '../api.ts'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' }

const BUTTON_BASE =
  'inline-flex items-center justify-center gap-1.5 rounded-md px-3 h-8 text-sm font-medium transition disabled:opacity-40 disabled:cursor-default'
const BUTTON_VARIANTS = {
  primary: 'bg-accent text-[#0b1320] hover:bg-[#6bb0ff]',
  ghost: 'border border-line text-fg hover:bg-white/8',
  danger: 'border border-danger/40 text-danger hover:bg-danger/10',
}

export function Button({ variant = 'ghost', className = '', ...props }: ButtonProps) {
  return <button type="button" className={`${BUTTON_BASE} ${BUTTON_VARIANTS[variant]} ${className}`} {...props} />
}

export function LinkButton({
  href,
  children,
  className = '',
  ...rest
}: {
  href: string
  children: ReactNode
  className?: string
  target?: string
  rel?: string
  title?: string
}) {
  return (
    <a href={href} className={`${BUTTON_BASE} ${BUTTON_VARIANTS.ghost} ${className}`} {...rest}>
      {children}
    </a>
  )
}

const BADGE_COLOURS: Record<Visibility, string> = {
  private: 'text-muted border-line',
  org: 'text-accent-2 border-accent-2/40',
  public: 'text-ok border-ok/40',
}

/** The visibility of an artifact, as the toolbar shows it. */
export function Badge({
  visibility,
  orgEditable,
  domain,
}: {
  visibility: Visibility
  orgEditable: boolean
  domain?: string | null
}) {
  const label = visibility === 'org' ? 'Org' : visibility === 'public' ? 'Public' : 'Private'
  const who = domain ? `Visible to ${domain}` : `${label} artifact`
  const title = orgEditable ? `${who}, editable by the organisation` : who
  return (
    <span
      className={`inline-flex h-5.5 items-center rounded-full border px-2 text-[11px] font-semibold uppercase tracking-wide ${BADGE_COLOURS[visibility]}`}
      title={title}
    >
      {label}
      {orgEditable ? <span className="ml-1 font-normal normal-case tracking-normal opacity-80">editable</span> : null}
    </span>
  )
}

/** A labelled form control: give the control the same `id`. */
export function Field({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 text-xs text-muted">
      <label htmlFor={id}>{label}</label>
      {children}
    </div>
  )
}

export const INPUT = 'h-9 rounded-md border border-line bg-bg px-2.5 text-sm text-fg outline-none focus:border-accent'

/** A select drawn with our own chevron, so the arrow sits inside the padding rather than against the edge. */
export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className="relative inline-grid">
      <select className={`${className ?? INPUT} appearance-none pr-8`} {...props} />
      <ChevronDown
        size={14}
        aria-hidden
        className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-muted"
      />
    </span>
  )
}

/** One option of a `RadioList`. */
export interface ListOption<T extends string> {
  value: T
  label: string
  hint?: string
}

/** A vertical radio group, one row per option with its label and an optional hint. */
export function RadioList<T extends string>({
  name,
  value,
  onChange,
  options,
}: {
  name: string
  value: T
  onChange: (value: T) => void
  options: ListOption<T>[]
}) {
  return (
    <div className="grid gap-1">
      {options.map((option) => (
        <label
          key={option.value}
          className="flex cursor-pointer items-baseline gap-2.5 rounded-md px-1.5 py-1 text-sm hover:bg-raised/60"
        >
          <input
            type="radio"
            className="accent-accent"
            name={name}
            value={option.value}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
          />
          <span>
            {option.label}
            {option.hint ? <span className="ml-1.5 text-xs text-muted">{option.hint}</span> : null}
          </span>
        </label>
      ))}
    </div>
  )
}

/** One option of a `RadioCards` group: the value, its name, an optional one-line hint and an icon or swatch. */
export interface CardOption<T extends string> {
  value: T
  label: string
  hint?: string
  icon: ReactNode
}

/**
 * A radio group drawn as a row of cards, for a handful of choices that deserve a hint or a preview rather than a
 * `<select>`. The radios are real inputs, visually hidden, so arrow keys, focus and form semantics are the browser's.
 */
export function RadioCards<T extends string>({
  label,
  name,
  value,
  onChange,
  options,
  columns,
}: {
  label: string
  name: string
  value: T
  onChange: (value: T) => void
  options: CardOption<T>[]
  /** Three cards with the icon beside the label, or four narrower ones with the icon above it. */
  columns: 3 | 4
}) {
  const stacked = columns === 4
  return (
    <fieldset className="grid gap-1 text-xs text-muted">
      <legend className="mb-1">{label}</legend>
      <div className={`grid gap-2 ${columns === 3 ? 'grid-cols-3' : 'grid-cols-4'}`}>
        {options.map((option) => (
          <label
            key={option.value}
            className="grid cursor-pointer gap-1.5 rounded-lg border border-line bg-bg p-2.5 text-fg hover:border-faint has-checked:border-accent has-checked:bg-accent/10 has-focus-visible:ring-2 has-focus-visible:ring-accent/60"
          >
            <input
              type="radio"
              className="sr-only"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
            />
            <span
              className={`flex gap-2 text-sm font-medium ${stacked ? 'flex-col items-center text-center text-xs' : 'items-center'}`}
            >
              <span className="text-muted">{option.icon}</span>
              {option.label}
            </span>
            {option.hint ? <span className="text-xs leading-snug text-muted">{option.hint}</span> : null}
          </label>
        ))}
      </div>
    </fieldset>
  )
}

/** A native dialog, shown as a modal while `open` is true; closes on Escape, the backdrop or `onClose`. */
export function Dialog({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])
  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: the click closes on the backdrop only; Escape is native
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose()
      }}
      className="m-auto w-[min(92vw,460px)] rounded-xl border border-line bg-panel p-6 text-fg shadow-2xl backdrop:bg-black/60"
    >
      <h2 className="mb-4 text-lg font-semibold">{title}</h2>
      {children}
    </dialog>
  )
}

export function Avatar({ name, picture, size = 28 }: { name: string; picture: string | null; size?: number }) {
  const style = { width: size, height: size }
  if (picture) {
    return (
      <img src={picture} alt="" referrerPolicy="no-referrer" className="rounded-full object-cover" style={style} />
    )
  }
  return (
    <span
      className="inline-flex items-center justify-center rounded-full bg-gradient-to-br from-accent to-accent-2 text-[11px] font-bold text-white"
      style={style}
    >
      {name.slice(0, 1).toUpperCase()}
    </span>
  )
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <output className="flex items-center gap-2 text-sm text-muted">
      <span className="size-3.5 animate-spin rounded-full border-2 border-line border-t-accent" />
      {label}
    </output>
  )
}

/** The brand: the star of the Penrose kite-and-dart tiling (the favicon, `app/public/favicon.svg`) and the name. */
export function Wordmark() {
  return (
    <span className="inline-flex items-center gap-2 text-sm font-semibold tracking-wide">
      <Star className="size-4" />
      OpenArtifact
    </span>
  )
}

/** The "star" vertex of the Penrose kite-and-dart tiling, five darts meeting at their apexes, in Pydantic
 * magenta: the same path as the favicon. */
export function Star({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 100 100" className={className} aria-hidden="true">
      <path
        d="M 50.00 8.39 L 66.71 31.39 L 93.75 40.18 L 77.04 63.18 L 77.04 91.61 L 50.00 82.82 L 22.96 91.61 L 22.96 63.18 L 6.25 40.18 L 33.29 31.39 Z"
        fill="#e620e9"
      />
    </svg>
  )
}
