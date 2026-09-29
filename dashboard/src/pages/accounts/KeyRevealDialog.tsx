import { useEffect, useState } from 'react'
import Button from '../../components/Button'
import ConfirmDialog from '../../components/ConfirmDialog'
import type { KeyReveal } from './useAccountsPage'

/** One-time key reveal. Confirm and cancel both just close it, and closing
 *  is the last time the key is on screen. */
export default function KeyRevealDialog({ reveal, onClose, onCopyError }: {
  reveal: KeyReveal | null
  onClose: () => void
  onCopyError: (message: string) => void
}) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!reveal) setCopied(false)
  }, [reveal])

  const copyKey = async (key: string) => {
    try {
      await navigator.clipboard.writeText(key)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      onCopyError('Could not copy — select the key and copy it by hand')
    }
  }

  return (
    <ConfirmDialog
      open={reveal != null}
      title={reveal?.title ?? ''}
      confirmLabel="I have copied it — close"
      onConfirm={onClose}
      onCancel={onClose}
    >
      <p>
        This key is shown <strong>once</strong>. Paste it into the EA's{' '}
        <span className="num">InpKey</span> input. If it is lost, rotate the
        key from the account's actions menu — the old one stops working at once.
      </p>
      <div className="flex items-center gap-2">
        <code
          aria-label="MT5 key"
          className="num flex-1 break-all rounded-inset border border-line-strong bg-paper px-3 py-2 text-xs text-ink"
        >
          {reveal?.key}
        </code>
        <Button
          variant="secondary"
          size="sm"
          className="shrink-0"
          onClick={() => { if (reveal) copyKey(reveal.key) }}
        >
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      {reveal?.download_url && (
        <a
          href={reveal.download_url}
          download
          className="inline-block text-sm font-medium text-brand hover:underline"
        >
          Download MirrorFleet.mq5
        </a>
      )}
      {reveal && reveal.install.length > 0 && (
        <ol className="list-decimal pl-5 space-y-1">
          {reveal.install.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}
    </ConfirmDialog>
  )
}
