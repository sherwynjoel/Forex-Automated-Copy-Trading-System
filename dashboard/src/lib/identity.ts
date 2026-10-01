import type { BadgeTone } from '../components/Badge'
import { BADGE_TONE, type StatusTone } from './investor'
import type {
  AccountRequestStatus, Gender, IdType, KycFileField, KycStatus, KycTextField, SignIn,
} from './types'

const KYC: Record<KycStatus, [string, StatusTone]> = {
  draft: ['Not submitted', 'quiet'],
  submitted: ['Under review', 'warn'],
  approved: ['Verified', 'ok'],
  rejected: ['Rejected', 'bad'],
}

export function kycLabel(status: KycStatus): string {
  return KYC[status][0]
}

export function kycBadge(status: KycStatus): BadgeTone {
  return BADGE_TONE[KYC[status][1]]
}

const REQUEST: Record<AccountRequestStatus, [string, StatusTone]> = {
  requested: ['Requested', 'warn'],
  fulfilled: ['Ready', 'ok'],
  rejected: ['Rejected', 'bad'],
  cancelled: ['Cancelled', 'quiet'],
}

export function requestLabel(status: AccountRequestStatus): string {
  return REQUEST[status][0]
}

export function requestBadge(status: AccountRequestStatus): BadgeTone {
  return BADGE_TONE[REQUEST[status][1]]
}

export const OUTCOME_LABELS: Record<SignIn['outcome'], string> = {
  password_ok: 'Password accepted',
  mpin_ok: 'Signed in',
  failed: 'Wrong password',
}

/** "Chrome on Windows" from a user agent; good enough to spot a stranger. */
export function deviceLabel(userAgent: string | null): string {
  if (!userAgent) return 'Unknown device'
  const ua = userAgent
  const browser = /Edg\//.test(ua) ? 'Edge'
    : /OPR\//.test(ua) ? 'Opera'
    : /Chrome\//.test(ua) ? 'Chrome'
    : /Firefox\//.test(ua) ? 'Firefox'
    : /Safari\//.test(ua) ? 'Safari'
    : 'Browser'
  // Android before Linux and iOS before macOS: their agents name both.
  const os = /Windows/.test(ua) ? 'Windows'
    : /Android/.test(ua) ? 'Android'
    : /iPhone|iPad/.test(ua) ? 'iOS'
    : /Mac OS X/.test(ua) ? 'macOS'
    : /Linux/.test(ua) ? 'Linux'
    : ''
  return os ? `${browser} on ${os}` : browser
}

export const PASSWORD_RULE =
  '8 to 32 characters, no spaces, with an upper-case letter, a lower-case letter and a digit'

/** The server's MT5 password policy (portal_identity.check_mt5_password). */
export function passwordProblem(p: string): string | null {
  const ok = /^[!-~]{8,32}$/.test(p) && /[A-Z]/.test(p) && /[a-z]/.test(p) && /[0-9]/.test(p)
  return ok ? null : `Use ${PASSWORD_RULE}`
}

// No 0/O, 1/l/I: the investor may read this off a screen into MetaTrader.
const UPPER = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
const LOWER = 'abcdefghijkmnopqrstuvwxyz'
const DIGITS = '23456789'

function randomIndex(n: number): number {
  const a = new Uint32Array(1)
  crypto.getRandomValues(a)
  return a[0] % n
}

/** A password that always passes passwordProblem: one of each class, the
 *  rest from all three, shuffled. */
export function generatePassword(length = 12): string {
  const all = UPPER + LOWER + DIGITS
  const chars = [UPPER, LOWER, DIGITS].map((set) => set[randomIndex(set.length)])
  while (chars.length < length) chars.push(all[randomIndex(all.length)])
  for (let i = chars.length - 1; i > 0; i--) {
    const j = randomIndex(i + 1)
    ;[chars[i], chars[j]] = [chars[j], chars[i]]
  }
  return chars.join('')
}

export const FIELD_LABELS: Record<KycTextField | KycFileField, string> = {
  full_name: 'Full name',
  gender: 'Gender',
  date_of_birth: 'Date of birth',
  phone: 'Phone',
  address_line: 'Address',
  area: 'Area',
  landmark: 'Landmark',
  city: 'City',
  state: 'State',
  postal_code: 'Postal code',
  country_residence: 'Country of residence',
  country_citizenship: 'Citizenship',
  id_type: 'ID type',
  id_number: 'ID number',
  id_front_file_id: 'ID front',
  id_back_file_id: 'ID back',
  address_proof_file_id: 'Proof of address',
  photo_file_id: 'Your photo',
}

export const GENDERS: readonly (readonly [Gender, string])[] = [
  ['male', 'Male'], ['female', 'Female'], ['other', 'Other'],
]

export const ID_TYPES: readonly (readonly [IdType, string])[] = [
  ['passport', 'Passport'], ['national_id', 'National ID card'], ['driving_licence', 'Driving licence'],
]
