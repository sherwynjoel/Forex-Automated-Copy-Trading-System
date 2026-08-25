import { expect, test, describe } from 'vitest'
import { unitsFor, priceForAmount, quoteCurrencyOf } from './protection'

describe('unitsFor', () => {
  test('gold: 0.01 lots of a 100-unit contract is 1 ounce', () => {
    // XAUUSD lot_size 10000 protocol units => 0.01 lots = 100 units = 1 oz.
    expect(unitsFor(0.01, 10_000)).toBe(1)
  })

  test('forex: 0.01 lots of a 10,000,000-unit contract is 1,000 base units', () => {
    expect(unitsFor(0.01, 10_000_000)).toBe(1_000)
  })

  test('refuses to guess when the contract size is unknown', () => {
    // An older API response has no lot_size. Returning a number here would
    // silently price the stop off a made-up contract size.
    expect(unitsFor(0.01, null)).toBeNull()
    expect(unitsFor(0.01, 0)).toBeNull()
    expect(unitsFor(0, 10_000)).toBeNull()
  })
})

describe('priceForAmount', () => {
  const units = unitsFor(0.01, 10_000)   // 1 oz of gold

  test('the case that started this: $1.50 on 0.01 lots of gold at 4652.84', () => {
    // A BUY wins as the price rises.
    expect(priceForAmount('BUY', 'tp', 4652.84, 1.5, units)).toBeCloseTo(4654.34, 5)
    expect(priceForAmount('BUY', 'sl', 4652.84, 1.5, units)).toBeCloseTo(4651.34, 5)
  })

  test('a SELL is the mirror image', () => {
    expect(priceForAmount('SELL', 'tp', 4652.84, 1.5, units)).toBeCloseTo(4651.34, 5)
    expect(priceForAmount('SELL', 'sl', 4652.84, 1.5, units)).toBeCloseTo(4654.34, 5)
  })

  test('a bigger position needs a smaller move for the same money', () => {
    const tenTimes = unitsFor(0.1, 10_000)          // 10 oz
    // $1.50 across 10 oz is 15 cents of price, not 1.50.
    expect(priceForAmount('BUY', 'tp', 4652.84, 1.5, tenTimes)).toBeCloseTo(4652.99, 5)
  })

  test('returns null rather than a wrong price when inputs are unusable', () => {
    expect(priceForAmount('BUY', 'tp', 4652.84, 1.5, null)).toBeNull()
    expect(priceForAmount('BUY', 'tp', 0, 1.5, units)).toBeNull()
    expect(priceForAmount('BUY', 'tp', 4652.84, 0, units)).toBeNull()
    expect(priceForAmount('BUY', 'tp', 4652.84, NaN, units)).toBeNull()
  })

  test('never produces a negative price', () => {
    // A stop wider than the instrument is worth is not a price level.
    expect(priceForAmount('BUY', 'sl', 10, 5000, unitsFor(0.01, 10_000))).toBeNull()
  })
})

describe('quoteCurrencyOf', () => {
  test('reads the quote side of a normal pair', () => {
    expect(quoteCurrencyOf('XAUUSD')).toBe('USD')
    expect(quoteCurrencyOf('USDJPY')).toBe('JPY')
    expect(quoteCurrencyOf('EURUSD')).toBe('USD')
  })

  test('says nothing when the name is not a six-letter pair', () => {
    // Indices and CFDs (US500, BTCUSD.spot) must not be guessed at -- a
    // wrong currency label on a money field is worse than no label.
    expect(quoteCurrencyOf('US500')).toBeNull()
    expect(quoteCurrencyOf('')).toBeNull()
    expect(quoteCurrencyOf(null)).toBeNull()
  })
})
