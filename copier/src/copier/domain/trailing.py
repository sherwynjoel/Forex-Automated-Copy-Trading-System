"""The trailing-stop ratchet: entry price + how far favourably price has
moved -> a new stop, never moving backward. A direct port of the same
step-trail formula the owner's Pine bots already run client-side
(rajan-dollar-bot.pine, ut-bot-2026-elite.pine) -- ported here so
MirrorFleet's own trailing behaves identically for a trader who already
understands those bots, whether the position came from one of them or
from any other indicator.
"""
import math


def compute_trailed_stop(side: str, entry_price: float, best_price: float,
                         trail_start_points: float, trail_step_points: float,
                         current_stop: float) -> float:
    """The stop a trailing-enabled position should carry right now.

    `best_price` is the best (most favourable) price the position has
    reached so far, not necessarily its current price -- callers own
    tracking that across ticks. Returns `current_stop` unchanged if
    trailing has not started yet (best_price has not moved
    trail_start_points past entry) or if the computed value would move
    the stop backward -- a stop only ever tightens.
    """
    if side == "BUY":
        favourable = best_price - entry_price
    else:
        favourable = entry_price - best_price

    if favourable < trail_start_points:
        return current_stop

    steps = math.floor((favourable - trail_start_points) / trail_step_points)
    distance = trail_start_points - trail_step_points + steps * trail_step_points

    if side == "BUY":
        candidate = entry_price + distance
        return max(candidate, current_stop)
    else:
        candidate = entry_price - distance
        return min(candidate, current_stop)
