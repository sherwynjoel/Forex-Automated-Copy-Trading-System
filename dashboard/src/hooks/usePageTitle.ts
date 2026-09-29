import { useEffect } from 'react'

const APP = 'MirrorFleet'

/** Titles the document "<title> · MirrorFleet" while the caller is mounted
 *  and restores the bare app name on unmount, so screen readers and tabs
 *  always say where the user is. */
export function usePageTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} · ${APP}` : APP
    return () => { document.title = APP }
  }, [title])
}
