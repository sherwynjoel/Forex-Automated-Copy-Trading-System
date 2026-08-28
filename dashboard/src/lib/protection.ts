/**
 * Turning a money amount into a stop/target PRICE.
 *
 * cTrader only accepts protection as a price level, but that is not how a
 * trader thinks about risk: "get me out at three dollars down" is the
 * intent, and 4651.34 is merely the arithmetic. Doing that sum by hand at
 * the ticket is where mistakes happen -- typing 1.5 into a field that
 * wanted 4654.34 is not a slip, it is the natural reading of the label.
 *
 * The engine values a position as
 *     pnl_quote = price_move * units,  units = protocolVolume / 100
 * (copier/src/copier/engine/state.py:unrealized_pnl_quote), so inverting
 * it for a target amount gives
 *     price_move = amount / units.
 *
 * IMPORTANT: the result is in the symbol's QUOTE currency. For XAUUSD on a
 * USD account that is dollars; for USDJPY it is yen. Callers must not
 * present a yen figure as dollars -- see quoteCurrencyOf().
 */

/** Base-currency units a lot count represents, or null if unknowable. */
export function unitsFor(lots: number, lotSize: number | null | undefined): number | null {
  if (!Number.isFinite(lots) || lots <= 0) return null
  if (!lotSize || !Number.isFinite(lotSize) || lotSize <= 0) return null
  return (lots * lotSize) / 100
}

/**
 * Round-trip commission on `units`, or 0 when it is not known.
 *
 * Zero is the honest unknown: it leaves the arithmetic exactly where it
 * has always been, so a symbol the account has never traded behaves as it
 * did yesterday rather than moving on a guess.
 */
export function feeFor(
  units: number | null | undefined,
  perUnit: number | null | undefined,
): number {
  if (units == null || perUnit == null) return 0
  if (!Number.isFinite(units) || !Number.isFinite(perUnit)) return 0
  if (units <= 0 || perUnit <= 0) return 0
  return perUnit * units
}

/**
 * The price at which an open position nets `amount`, after commission.
 *
 * THE AMOUNT IS WHAT ARRIVES, not what the price move is worth. Those are
 * different numbers and the gap is not small: a $1.50 target on 0.01 lots
 * of gold paid $1.26, because inverting the P&L formula gives GROSS profit
 * and the broker then takes its cut out of it. Fixed per lot, the error is
 * invisible on a big trade and a fifth of a small one.
 *
 * The two sides correct in OPPOSITE directions, which is the whole subtlety:
 *
 *   TAKE PROFIT -- commission is subtracted from the win, so the move must
 *   be worth amount + fee. The target sits FURTHER from entry.
 *
 *   STOP LOSS -- commission is added to the loss, so the move may only be
 *   worth amount - fee. The stop sits CLOSER to entry.
 *
 * Getting that inversion backwards would widen every stop by twice the
 * commission instead of tightening it, which is why both directions are
 * pinned by tests.
 *
 * @param side      BUY or SELL
 * @param kind      'tp' for the winning side, 'sl' for the losing side
 * @param entry     the price the order is expected to fill at
 * @param amount    money in the symbol's quote currency, always positive,
 *                  understood as NET of commission
 * @param units     from unitsFor()
 * @param digits    the symbol's quoted decimal places
 * @param fee       round-trip commission from feeFor(); 0 or omitted keeps
 *                  the old gross behaviour
 * @returns the price, or null when it cannot be computed -- including a
 *          stop smaller than the commission it would have to absorb, which
 *          is not a tight stop but an impossible one
 */
export function priceForAmount(
  side: 'BUY' | 'SELL',
  kind: 'tp' | 'sl',
  entry: number,
  amount: number,
  units: number | null,
  digits?: number | null,
  fee?: number | null,
): number | null {
  if (units == null || !Number.isFinite(entry) || entry <= 0) return null
  if (!Number.isFinite(amount) || amount <= 0) return null

  const cost = fee != null && Number.isFinite(fee) && fee > 0 ? fee : 0
  // What the PRICE has to be worth for `amount` to survive the commission.
  const gross = kind === 'tp' ? amount + cost : amount - cost
  // A stop at or under the commission cannot exist: the position is
  // already down by the fee the moment it opens, so no price closes it for
  // less. Refusing is the only honest answer -- silently widening it to
  // the fee would lose the operator more than they asked to risk.
  if (!(gross > 0)) return null

  const move = gross / units
  // A BUY wins as price rises and loses as it falls; a SELL is the mirror.
  const up = (side === 'BUY') === (kind === 'tp')
  const price = up ? entry + move : entry - move

  // A stop below zero is not a price. Refuse rather than send nonsense.
  if (!(price > 0)) return null

  // ROUND TO WHAT THE BROKER QUOTES. Binary floating point turns
  // 4587.48 - 2.3 into 4585.179999999999, and cTrader answered
  // INVALID_REQUEST to a gold stop carrying twelve decimals when the
  // symbol is quoted to two -- so the protection simply never took, with
  // nothing on screen to say why.
  //
  // Without a digit count, 8 places still removes the artifact (which
  // appears far further out) while preserving any realistic precision.
  const places = digits != null && Number.isFinite(digits) ? digits : 8
  return Number(price.toFixed(places))
}

/**
 * The currency an amount would actually be denominated in, inferred from
 * the symbol name. Returns null when the name is not a recognisable pair,
 * in which case the caller should not claim to know.
 */
export function quoteCurrencyOf(symbol: string | null | undefined): string | null {
  if (!symbol) return null
  const plain = symbol.replace(/[^A-Za-z]/g, '').toUpperCase()
  return plain.length === 6 ? plain.slice(3) : null
}

/**
 * Units for a position already on the book.
 *
 * An open position reports its size in protocol volume, so its unit count
 * needs no contract size at all -- the engine's own divisor is enough.
 * That makes an amount-denominated stop exact on the Positions screen,
 * where the ticket has to infer it from lots and lot_size.
 */
export function unitsFromVolume(protocolVolume: number | null | undefined): number | null {
  if (protocolVolume == null) return null
  if (!Number.isFinite(protocolVolume) || protocolVolume <= 0) return null
  return protocolVolume / 100
}
