import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import NextStep from './NextStep'

// Deliberately unmocked: the real LANDING_FACTS are blank until the owner
// confirms a mailbox, so a pending state must not offer a contact line.
test('the pending state shows no contact line while the support email is blank', () => {
  render(<NextStep title="Not yet">Your admin links your trading account.</NextStep>)
  expect(screen.getByText('Your admin links your trading account.')).toBeInTheDocument()
  expect(screen.queryByText(/questions\?/i)).toBeNull()
  expect(screen.queryByRole('link')).toBeNull()
})
