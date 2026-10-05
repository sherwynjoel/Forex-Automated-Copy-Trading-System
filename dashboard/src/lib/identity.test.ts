import { expect, test } from 'vitest'
import {
  deviceLabel, fieldValue, generatePassword, kycBadge, kycLabel, passwordProblem, requestBadge, requestLabel,
} from './identity'

test('KYC and request statuses read as words with a tone', () => {
  expect(kycLabel('draft')).toBe('Not submitted')
  expect(kycLabel('submitted')).toBe('Under review')
  expect(kycLabel('approved')).toBe('Verified')
  expect(kycLabel('rejected')).toBe('Rejected')
  expect(kycBadge('approved')).toBe('profit')
  expect(kycBadge('rejected')).toBe('loss')
  expect(requestLabel('fulfilled')).toBe('Ready')
  expect(requestBadge('requested')).toBe('warn')
})

test('passwordProblem mirrors the server policy', () => {
  for (const ok of ['Abcdefg1', 'Pa$$w0rd!', `Z9${'x'.repeat(30)}`]) expect(passwordProblem(ok)).toBeNull()
  for (const bad of ['Abcdef1', 'abcdefg1', 'ABCDEFG1', 'Abcdefgh', 'Abc defg1', `A1${'x'.repeat(31)}`]) {
    expect(passwordProblem(bad)).toBe(
      'Use 8 to 32 characters, no spaces, with an upper-case letter, a lower-case letter and a digit')
  }
})

test('a generated password always passes the policy and differs each time', () => {
  const seen = new Set<string>()
  for (let i = 0; i < 50; i++) {
    const p = generatePassword()
    expect(p).toHaveLength(12)
    expect(passwordProblem(p)).toBeNull()
    seen.add(p)
  }
  expect(seen.size).toBe(50)
})

test('fieldValue shows choices as words and nothing as a dash', () => {
  expect(fieldValue('id_type', 'national_id')).toBe('National ID card')
  expect(fieldValue('gender', 'female')).toBe('Female')
  expect(fieldValue('city', 'Coimbatore')).toBe('Coimbatore')
  expect(fieldValue('city', null)).toBe('—')
})

test('deviceLabel names the browser and the system', () => {
  expect(deviceLabel(null)).toBe('Unknown device')
  expect(deviceLabel('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36'))
    .toBe('Chrome on Windows')
  expect(deviceLabel('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1'))
    .toBe('Safari on iOS')
  expect(deviceLabel('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/129.0 Mobile Safari/537.36'))
    .toBe('Chrome on Android')
  expect(deviceLabel('curl/8.0')).toBe('Browser')
})
