import '@testing-library/jest-dom'

// jsdom has no matchMedia; motion's useReducedMotion and our own
// prefers-reduced-motion checks read it. Answer "no preference" everywhere.
if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })
}
