import { render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import PageHeader from './PageHeader'

afterEach(() => { document.title = 'MirrorFleet' })

test('renders the single h1, the subtitle and the actions, and titles the document', () => {
  const view = render(
    <PageHeader title="Accounts" subtitle="Acme desk" actions={<button>Connect</button>}>
      <p>toolbar</p>
    </PageHeader>
  )
  expect(screen.getByRole('heading', { level: 1, name: 'Accounts' })).toHaveAttribute('id', 'page-title')
  expect(screen.getByText('Acme desk')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Connect' })).toBeInTheDocument()
  expect(screen.getByText('toolbar')).toBeInTheDocument()
  expect(document.title).toBe('Accounts · MirrorFleet')
  view.unmount()
  expect(document.title).toBe('MirrorFleet')
})
