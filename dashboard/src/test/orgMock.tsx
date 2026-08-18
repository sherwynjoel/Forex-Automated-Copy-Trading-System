import { vi } from 'vitest'
import type { Role } from '../lib/roles'
import type { Me, OrgSummary } from '../lib/types'

/**
 * Builds the value `useOrg()` returns, for tests that mock `../lib/org`.
 *
 * Vitest hoists `vi.mock(...)` calls above every import in the file
 * (including ones that only exist to feed the factory), so the factory
 * itself cannot reference a `vi.fn()` imported from this module -- that was
 * tried and throws "Cannot access '__vi_import_N__' before initialization".
 * The mock registration therefore still has to live in each test file via
 * `vi.hoisted()`:
 *
 *   const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
 *   vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))
 *   import { mockUseOrg } from '../test/orgMock'
 *   ...
 *   useOrgMock.mockReturnValue(mockUseOrg('admin'))
 *
 * What this helper extracts is the fiddly, error-prone part: assembling a
 * `{orgId, role, org, me, refreshMe}` object that actually matches the real
 * `useOrg()` contract.
 */
interface OrgMockOptions {
  meUserId?: number
  orgName?: string
  orgs?: OrgSummary[]
}

export function mockUseOrg(role: Role, orgId = 1, options: OrgMockOptions = {}) {
  const { meUserId = 1, orgName = 'Acme', orgs } = options
  const me: Me = {
    user: { id: meUserId, email: 'user@example.com', display_name: 'Test User' },
    orgs: orgs ?? [{ id: orgId, name: orgName, role }],
  }
  return {
    orgId,
    role,
    org: { id: orgId, name: orgName, role } as OrgSummary,
    me,
    refreshMe: vi.fn(),
  }
}
