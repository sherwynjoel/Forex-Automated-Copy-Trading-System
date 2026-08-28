import { expect, test, describe } from 'vitest'
import { unitsFor, unitsFromVolume, priceForAmount, quoteCurrencyOf, feeFor } from './protection'

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

describe('unitsFromVolume', () => {
  test('an open 0.01-lot gold position reports 100 protocol units = 1 ounce', () => {
    expect(unitsFromVolume(100)).toBe(1)
  })

  test('agrees with the lots-and-contract-size route', () => {
    // Both paths must value the same position identically, or an amount
    // would mean one thing on the ticket and another on Positions.
    expect(unitsFromVolume(100)).toBe(unitsFor(0.01, 10_000))
  })

  test('refuses nonsense rather than returning a misleading zero', () => {
    expect(unitsFromVolume(0)).toBeNull()
    expect(unitsFromVolume(null)).toBeNull()
    expect(unitsFromVolume(NaN)).toBeNull()
  })
})

describe('amount protection on an open position', () => {
  test('$1.50 on a 0.01-lot BUY of gold entered at 4631.31', () => {
    const units = unitsFromVolume(100)
    expect(priceForAmount('BUY', 'tp', 4631.31, 1.5, units)).toBeCloseTo(4632.81, 5)
    expect(priceForAmount('BUY', 'sl', 4631.31, 1.5, units)).toBeCloseTo(4629.81, 5)
  })
})

describe('rounding to the price the broker will accept', () => {
  test('the exact case cTrader refused: gold to 2 decimals', () => {
    // Live rejection: stop_loss 4585.179999999999 -> INVALID_REQUEST.
    // 4587.48 - (62.1 / 27) lands on a binary artifact, and a symbol
    // quoted to two decimals will not take twelve.
    const price = priceForAmount('BUY', 'sl', 4587.48, 62.1, 27, 2)
    expect(price).toBe(4585.18)
    expect(String(price)).not.toContain('99999')
  })

  test('five-decimal pairs keep their precision', () => {
    // EURUSD: 0.01 lots of a 10,000,000 contract = 1,000 units.
    const price = priceForAmount('BUY', 'tp', 1.08431, 1.5, 1000, 5)
    expect(price).toBe(1.08581)
  })

  test('without a digit count the artifact is still removed', () => {
    const price = priceForAmount('BUY', 'sl', 4587.48, 62.1, 27)
    expect(String(price)).not.toContain('99999')
    expect(price).toBeCloseTo(4585.18, 6)
  })

  test('rounding never turns a valid price into zero or a negative', () => {
    // A stop rounded to nothing would be sent as 0 and silently clear the
    // protection instead of setting it.
    expect(priceForAmount('BUY', 'sl', 0.5, 0.4, 1, 2)).toBeCloseTo(0.1, 6)
    expect(priceForAmount('BUY', 'sl', 10, 5000, 1, 2)).toBeNull()
  })
})

describe('an amount is what ARRIVES, not what the price move is worth', () => {
  // The trade that forced this: XAUUSD, 0.01 lots (one ounce), SELL at
  // 4615.51, take profit asked for as $1.50. It paid $1.26. The broker
  // charged $0.28 round trip and the target had only ever covered the
  // gross move. Real trade, real numbers -- position 313386385, 27 Aug 2026.
  const oz = unitsFor(0.01, 10_000)   // 1 ounce
  const fee = 0.28                    // what the broker actually took

  test('the take profit moves FURTHER out, to clear the commission', () => {
    // Without the fee it stopped at 1.50 of gross and paid 1.22 net.
    expect(priceForAmount('SELL', 'tp', 4615.51, 1.5, oz, 2)).toBeCloseTo(4614.01, 5)
    // With it, the move is worth 1.50 + 0.28, so 1.50 survives the charge.
    expect(priceForAmount('SELL', 'tp', 4615.51, 1.5, oz, 2, fee)).toBeCloseTo(4613.73, 5)
  })

  test('the stop loss moves CLOSER, because commission ADDS to a loss', () => {
    // This is the half that was quietly costing money: a "1.50" stop lost
    // 1.50 of price and then paid 0.28 on top -- 1.78 out of the account.
    expect(priceForAmount('SELL', 'sl', 4615.51, 1.5, oz, 2)).toBeCloseTo(4617.01, 5)
    // Allowing for it, the price may only move 1.22 before closing.
    expect(priceForAmount('SELL', 'sl', 4615.51, 1.5, oz, 2, fee)).toBeCloseTo(4616.73, 5)
  })

  test('the two corrections go in OPPOSITE directions', () => {
    // Pinned on its own because getting this inversion backwards is the
    // one mistake that would widen every stop instead of tightening it,
    // and it would look plausible on the screen either way.
    const entry = 4615.51
    const grossTp = priceForAmount('BUY', 'tp', entry, 1.5, oz, 2)!
    const netTp = priceForAmount('BUY', 'tp', entry, 1.5, oz, 2, fee)!
    const grossSl = priceForAmount('BUY', 'sl', entry, 1.5, oz, 2)!
    const netSl = priceForAmount('BUY', 'sl', entry, 1.5, oz, 2, fee)!

    // A BUY takes profit above entry: further means higher.
    expect(netTp).toBeGreaterThan(grossTp)
    // A BUY stops out below entry: closer means higher too.
    expect(netSl).toBeGreaterThan(grossSl)
    // ...but for opposite reasons, so both sit further from their old spot
    // in the direction that costs the operator less.
    expect(netTp - grossTp).toBeCloseTo(fee, 5)
    expect(netSl - grossSl).toBeCloseTo(fee, 5)
  })

  test('a stop smaller than the commission is refused, not silently widened', () => {
    // The position is already down by the fee the moment it opens, so no
    // price closes it for less. Quietly using the fee instead would risk
    // more than the operator asked to risk.
    expect(priceForAmount('BUY', 'sl', 4615.51, 0.28, oz, 2, fee)).toBeNull()
    expect(priceForAmount('BUY', 'sl', 4615.51, 0.1, oz, 2, fee)).toBeNull()
    // A target that small is merely slow, not impossible.
    expect(priceForAmount('BUY', 'tp', 4615.51, 0.1, oz, 2, fee)).not.toBeNull()
  })

  test('an unknown rate leaves the arithmetic exactly as it was', () => {
    // A symbol never traded on the account must behave as it did before
    // this feature existed -- no adjustment beats a guessed one.
    const asBefore = priceForAmount('BUY', 'tp', 4615.51, 1.5, oz, 2)
    expect(priceForAmount('BUY', 'tp', 4615.51, 1.5, oz, 2, 0)).toBe(asBefore)
    expect(priceForAmount('BUY', 'tp', 4615.51, 1.5, oz, 2, null)).toBe(asBefore)
    expect(priceForAmount('BUY', 'tp', 4615.51, 1.5, oz, 2, undefined)).toBe(asBefore)
    expect(priceForAmount('BUY', 'tp', 4615.51, 1.5, oz, 2, NaN)).toBe(asBefore)
  })

  test('the result still respects the symbol precision', () => {
    // Rounding is what made the broker refuse these outright; adding the
    // fee must not reintroduce a twelve-decimal price.
    const price = priceForAmount('SELL', 'tp', 4573.27, 1.5, oz, 2, 0.2777)!
    expect(price).toBe(Number(price.toFixed(2)))
  })
})

describe('feeFor', () => {
  test('scales with position size', () => {
    expect(feeFor(1, 0.28)).toBeCloseTo(0.28, 10)
    expect(feeFor(10, 0.28)).toBeCloseTo(2.8, 10)
  })

  test('an unknown rate costs nothing rather than guessing', () => {
    expect(feeFor(1, null)).toBe(0)
    expect(feeFor(1, undefined)).toBe(0)
    expect(feeFor(null, 0.28)).toBe(0)
    expect(feeFor(0, 0.28)).toBe(0)
    expect(feeFor(1, NaN)).toBe(0)
  })
})
