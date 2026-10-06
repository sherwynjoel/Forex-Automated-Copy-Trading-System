import { formatWhen } from '../../lib/format'
import type { TicketMessage } from '../../lib/types'

/** One ticket's thread, oldest first: who wrote, when, the text and its
 *  images. The investor sees "You" and "Support desk"; the desk sees names.
 *  `fileUrl` is the side's own file route (investor: own files; desk: org). */
export default function TicketMessages({ messages, fileUrl, viewer }: {
  messages: TicketMessage[]
  fileUrl: (fileId: number) => string
  viewer: 'investor' | 'desk'
}) {
  const who = (m: TicketMessage) => m.from_desk
    ? (viewer === 'investor' ? 'Support desk' : `${m.author_name ?? 'Admin'} (desk)`)
    : (viewer === 'investor' ? 'You' : m.author_name ?? 'Investor')
  return (
    <ol className="space-y-3">
      {messages.map((m) => (
        <li key={m.id} className="inset p-3 space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="font-semibold text-ink">{who(m)}</span>
            <span className="num text-ink-faint">{formatWhen(m.created_at)}</span>
          </div>
          <p className="text-sm text-ink whitespace-pre-wrap">{m.body}</p>
          {m.file_ids.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {m.file_ids.map((id, i) => (
                <a key={id} href={fileUrl(id)} target="_blank" rel="noreferrer"
                   aria-label={`Open image ${i + 1} of this message`}>
                  <img src={fileUrl(id)} alt={`Image ${i + 1}`}
                       className="h-24 w-24 rounded-inset border border-line object-cover" />
                </a>
              ))}
            </div>
          )}
        </li>
      ))}
    </ol>
  )
}
