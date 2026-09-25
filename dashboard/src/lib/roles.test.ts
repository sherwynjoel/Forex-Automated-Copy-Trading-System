import { describe, expect, it } from 'vitest'
import { can, roleLabel, OFFERED_ROLES } from './roles'

describe('can', () => {
  it('gates trade, control and member management at admin', () => {
    for (const action of ['trade', 'control', 'manage_members'] as const) {
      expect(can('investor', action)).toBe(false)
      expect(can('viewer', action)).toBe(false)
      expect(can('admin', action)).toBe(true)
    }
  })
  it('denies for missing or unknown roles', () => {
    expect(can(null, 'trade')).toBe(false)
    expect(can(undefined, 'control')).toBe(false)
    expect(can('owner' as never, 'control')).toBe(false)
  })
})

describe('roleLabel', () => {
  it('names the three roles and passes anything else through', () => {
    expect(roleLabel('admin')).toBe('Admin')
    expect(roleLabel('viewer')).toBe('Viewer')
    expect(roleLabel('investor')).toBe('Investor')
    expect(roleLabel('owner')).toBe('owner')
  })
})

describe('OFFERED_ROLES', () => {
  it('offers exactly admin, viewer and investor', () => {
    expect(OFFERED_ROLES).toEqual(['admin', 'viewer', 'investor'])
  })
})
