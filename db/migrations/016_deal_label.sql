-- The History page groups a fleet-wide trade by its order's `label`
-- ("copy:m<master_position_id>"), read from HistoricalOrder.label. cTrader
-- orders carry this live from the broker; MT5's order_history is
-- synthesized from stored deals, and the `deals` table had nowhere to keep
-- it -- so every MT5 copy showed under no master trade at all. The value
-- comes from the position's own comment, which the outbox already sets to
-- exactly this string when it opens a copy.
ALTER TABLE deals ADD COLUMN label TEXT;
