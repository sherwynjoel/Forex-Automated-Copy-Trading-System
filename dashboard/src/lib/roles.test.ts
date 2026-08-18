import { describe, expect, it } from 'vitest'
import { can } from './roles'

describe('can', () => {
  it('gates trade at trader', () => {
    expect(can('viewer', 'trade')).toBe(false)
    expect(can('trader', 'trade')).toBe(true)
    expect(can('admin', 'trade')).toBe(true)
    expect(can('owner', 'trade')).toBe(true)
  })
  it('gates control at admin', () => {
    expect(can('trader', 'control')).toBe(false)
    expect(can('admin', 'control')).toBe(true)
  })
  it('gates member management at owner', () => {
    expect(can('admin', 'manage_members')).toBe(false)
    expect(can('owner', 'manage_members')).toBe(true)
  })
  it('denies for missing role', () => {
    expect(can(null, 'trade')).toBe(false)
    expect(can(undefined, 'control')).toBe(false)
  })
})
