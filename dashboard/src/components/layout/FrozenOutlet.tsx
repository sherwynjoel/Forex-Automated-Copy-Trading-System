import { useState } from 'react'
import { useOutlet } from 'react-router-dom'

/** Captures the outlet element once per mount so an exiting route keeps
 *  rendering its own page while the new one enters beside it. */
export default function FrozenOutlet() {
  const outlet = useOutlet()
  const [frozen] = useState(outlet)
  return frozen
}
