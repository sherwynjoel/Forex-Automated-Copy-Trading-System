-- What the broker ACTUALLY charged, learned from closed positions.
--
-- A money-denominated stop or target inverts the P&L formula:
-- price_move = amount / units. That gives GROSS profit, and the broker
-- then takes its commission out of it, so "$1.50 take profit" paid $1.26
-- and "$1.50 stop loss" lost $1.78. The error is fixed per lot, so it is
-- invisible on a large trade and eats a fifth of a small one.
--
-- Correcting it needs the commission. cTrader does publish one on every
-- symbol -- ProtoOASymbol.commission, .commissionType, .minCommission,
-- .preciseTradingCommissionRate -- but the Python SDK ships generated
-- descriptors with source_code_info stripped, so the wheel contains no
-- comment, docstring or constant stating the scale of any of them, and
-- nothing in this repo has ever read them. Whether `commission = 30` is
-- thirty dollars, thirty cents or thirty per million is not answerable
-- from anything we ship, and guessing the exponent wrong moves a real
-- stop by a factor of a hundred.
--
-- A closed position needs no such guess. Every deal reports the money the
-- broker took, already scaled by its own moneyDigits, so summing a
-- position's deals gives the round trip as an observation rather than a
-- derivation. This table is where those observations live.

CREATE TABLE symbol_commission (
    account_id BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    symbol_id BIGINT NOT NULL,

    -- Round-trip commission for ONE unit of the symbol's base asset, in
    -- the account's deposit currency. "Unit" is protocol volume / 100 --
    -- the same divisor the P&L engine uses (engine/state.py), so a caller
    -- holding `units` multiplies and is done, with no contract size, no
    -- lot arithmetic and no currency conversion in between.
    --
    -- Stored per ACCOUNT, not per symbol name: commission is a term of
    -- the account's own agreement with the broker, and a fleet routinely
    -- mixes raw-spread and standard accounts whose rates differ.
    per_unit DOUBLE PRECISION NOT NULL CHECK (per_unit >= 0),

    -- How many complete round trips the figure was taken from. One
    -- sample is worth using -- it is still the broker's own number -- but
    -- it is worth knowing when a rate rests on a single trade.
    sample_count INTEGER NOT NULL CHECK (sample_count > 0),

    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (account_id, symbol_id)
);
