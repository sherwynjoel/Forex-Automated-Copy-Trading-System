import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import './index.css'

// A tab left open across a deploy asks for chunks that no longer exist;
// Vite reports the failed preload here, and a reload fetches the new build.
window.addEventListener('vite:preloadError', (e) => {
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
