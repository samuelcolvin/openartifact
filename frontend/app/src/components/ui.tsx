/** Small building blocks shared by the pages: buttons, badges, form fields, a native dialog, an avatar. */

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

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={INPUT} {...props} />
}

export function Checkbox({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string
  checked: boolean
  onChange: (value: boolean) => void
  disabled?: boolean
}) {
  return (
    <label className={`flex items-center gap-2 text-sm ${disabled ? 'opacity-40' : ''}`}>
      <input
        type="checkbox"
        className="accent-accent"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
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

export function Wordmark() {
  return (
    <span className="inline-flex items-center gap-2 text-sm font-semibold tracking-wide">
      <span className="size-3.5 rounded bg-gradient-to-br from-accent to-accent-2" />
      OpenArtifact
    </span>
  )
}
