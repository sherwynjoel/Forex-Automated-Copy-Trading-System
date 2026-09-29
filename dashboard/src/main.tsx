import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import './index.css'

// A tab left open across a deploy asks for chunks that no longer exist;
// Vite reports the failed preload here, and a reload fetches the new build.
// At most once per ten seconds: if the fresh build lacks the asset too, the
// error goes on to ChunkBoundary's Reload card instead of a reload loop.
const CHUNK_RELOAD_KEY = 'mf.chunkReloadAt'
window.addEventListener('vite:preloadError', (e) => {
  let last = 0
  try { last = Number(sessionStorage.getItem(CHUNK_RELOAD_KEY)) || 0 } catch { /* storage blocked */ }
  if (Date.now() - last < 10_000) return
  try { sessionStorage.setItem(CHUNK_RELOAD_KEY, String(Date.now())) } catch {
    // Without storage the guard cannot hold across the reload: let the
    // boundary offer it rather than risk a loop.
    return
  }
  e.preventDefault()
  location.reload()
})

// The wash animates for as long as the tab is visible, and not otherwise.
const syncPaused = () => {
  document.documentElement.dataset.paused = document.hidden ? 'true' : ''
}
document.addEventListener('visibilitychange', syncPaused)
syncPaused()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
