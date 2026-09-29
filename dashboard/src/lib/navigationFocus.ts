let pending = false

/** Layout sets this after a client-side navigation; the next page heading to mount claims focus. */
export function markNavigated(): void { pending = true }

export function consumePendingFocus(): boolean {
  const p = pending
  pending = false
  return p
}
