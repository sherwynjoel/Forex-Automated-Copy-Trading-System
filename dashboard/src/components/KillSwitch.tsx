import { useState } from 'react'
import { api } from '../lib/api'
import type { Settings } from '../lib/types'

interface KillSwitchProps {
  settings: Settings
  onUpdate: (settings: Settings) => void
}

export default function KillSwitch({ settings, onUpdate }: KillSwitchProps) {
  const [isLoading, setIsLoading] = useState(false)

  const handleToggleCopying = async () => {
    const newState = !settings.copying_enabled
    const message = newState
      ? 'Are you sure you want to resume copying?'
      : 'Are you sure you want to stop copying? This will pause all active trades.'

    if (!window.confirm(message)) {
      return
    }

    try {
      setIsLoading(true)
      await api('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ copying_enabled: newState }),
      })
      onUpdate({ ...settings, copying_enabled: newState })
    } catch (err) {
      console.error('Failed to update copying status:', err)
    } finally {
      setIsLoading(false)
    }
  }

  const buttonText = settings.copying_enabled ? 'STOP COPYING' : 'RESUME COPYING'
  const buttonColor = settings.copying_enabled ? 'bg-red-600 hover:bg-red-700' : 'bg-green-600 hover:bg-green-700'

  return (
    <div className="flex items-center gap-4">
      {settings.dry_run && (
        <div className="px-3 py-1 bg-yellow-100 text-yellow-800 text-sm font-semibold rounded-full">
          DRY RUN
        </div>
      )}
      <button
        onClick={handleToggleCopying}
        disabled={isLoading}
        className={`px-6 py-2 text-white font-bold rounded-lg ${buttonColor} disabled:opacity-50 disabled:cursor-not-allowed transition-colors`}
      >
        {buttonText}
      </button>
    </div>
  )
}
