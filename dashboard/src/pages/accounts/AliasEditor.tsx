import { useState } from 'react'
import Badge from '../../components/Badge'
import Button from '../../components/Button'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import type { SymbolAliases } from '../../lib/types'

type AliasRow = SymbolAliases['aliases'][number]

export default function AliasEditor({ aliases, error, canView, canEdit, onSave }: {
  aliases: SymbolAliases | null
  error: string | null
  /** Reading the mapping needs the admin role (the api's `trade` action). */
  canView: boolean
  canEdit: boolean
  /** One PUT for one canonical name; an empty broker name removes it. */
  onSave: (canonical: string, brokerName: string) => Promise<void>
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [newAlias, setNewAlias] = useState({ canonical: '', broker_name: '' })

  const handleBlur = async (row: AliasRow) => {
    const draft = drafts[row.canonical]
    if (draft === undefined || draft.trim() === row.broker_name) return
    await onSave(row.canonical, draft.trim())
    setDrafts((prev) => {
      const next = { ...prev }
      delete next[row.canonical]
      return next
    })
  }

  const handleAdd = () => {
    // Canonical names are what master events carry: upper-case.
    const canonical = newAlias.canonical.trim().toUpperCase()
    const brokerName = newAlias.broker_name.trim()
    if (!canonical || !brokerName) return
    setNewAlias({ canonical: '', broker_name: '' })
    void onSave(canonical, brokerName)
  }

  return (
    <section>
      <h3 className="desk-label mb-2">Symbol mapping</h3>
      {!canView ? (
        <p className="text-sm text-ink-faint">Only an admin can see the mapping.</p>
      ) : error ? (
        <p className="text-sm text-loss-deep">{error}</p>
      ) : !aliases ? (
        <Loading lines={2} label="Loading the mapping" />
      ) : (
        <>
          {aliases.aliases.length === 0 ? (
            <p className="text-sm text-ink-faint">
              Nothing mapped yet — the terminal sends its symbol list when
              the EA first connects.
            </p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {aliases.aliases.map((row) => (
                <li key={row.canonical} className="flex items-center justify-between gap-3">
                  <span className="num text-ink">{row.canonical}</span>
                  <span className="flex items-center gap-2">
                    {canEdit ? (
                      <Input
                        type="text"
                        num
                        aria-label={`Broker symbol for ${row.canonical}`}
                        list="mt5-broker-symbols"
                        value={drafts[row.canonical] ?? row.broker_name}
                        onChange={(e) =>
                          setDrafts((prev) => ({ ...prev, [row.canonical]: e.target.value }))}
                        onBlur={() => handleBlur(row)}
                        className="w-28 text-right"
                      />
                    ) : (
                      <span className="num text-ink">{row.broker_name}</span>
                    )}
                    <Badge tone={row.source === 'manual' ? 'brand' : 'neutral'}>{row.source}</Badge>
                  </span>
                </li>
              ))}
            </ul>
          )}
          {canEdit && (
            <div className="mt-3 flex items-center gap-2">
              <Input
                type="text"
                num
                aria-label="New canonical symbol"
                placeholder="XAUUSD"
                value={newAlias.canonical}
                onChange={(e) => setNewAlias((prev) => ({ ...prev, canonical: e.target.value }))}
                className="w-24"
              />
              <span className="text-ink-faint">→</span>
              <Input
                type="text"
                num
                aria-label="New broker symbol"
                placeholder="GOLD.r"
                list="mt5-broker-symbols"
                value={newAlias.broker_name}
                onChange={(e) => setNewAlias((prev) => ({ ...prev, broker_name: e.target.value }))}
                className="w-28"
              />
              <Button
                variant="secondary"
                size="sm"
                onClick={handleAdd}
                disabled={!newAlias.canonical.trim() || !newAlias.broker_name.trim()}
              >
                Add mapping
              </Button>
            </div>
          )}
          <datalist id="mt5-broker-symbols">
            {aliases.broker_symbols.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
          <p className="mt-2 text-xs text-ink-faint">
            Clearing a broker name removes the mapping; a manual entry is
            never overwritten by the auto-matcher.
          </p>
        </>
      )}
    </section>
  )
}
