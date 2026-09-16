"""The trailing ratchet formula: a direct port of the same step-trail
math already used in the owner's Pine bots (rajan-dollar-bot.pine,
ut-bot-2026-elite.pine), so a rule configured here behaves identically
to what the trader already understands from those scripts.
"""
from copier.domain.trailing import compute_trailed_stop


def test_long_before_trail_start_stop_does_not_move():
    # price has moved 3 points favourably, trail starts at 5 -- too early
    new_stop = compute_trailed_stop(
        side="BUY", entry_price=100.0, best_price=103.0,
        trail_start_points=5.0, trail_step_points=1.0, current_stop=95.0)
    assert new_stop == 95.0


def test_long_exactly_at_trail_start_moves_to_first_step():
    # maxFav == trail_start exactly: new_stop = entry + (start - step + 0)
    new_stop = compute_trailed_stop(
        side="BUY", entry_price=100.0, best_price=105.0,
        trail_start_points=5.0, trail_step_points=1.0, current_stop=95.0)
    assert new_stop == 104.0  # 100 + (5 - 1 + floor(0/1)*1)


def test_long_past_trail_start_steps_in_whole_increments():
    # maxFav = 107.5, so 2.5 points past start -> floor(2.5/1) = 2 steps
    new_stop = compute_trailed_stop(
        side="BUY", entry_price=100.0, best_price=107.5,
        trail_start_points=5.0, trail_step_points=1.0, current_stop=95.0)
    assert new_stop == 106.0  # 100 + (5 - 1 + 2*1)


def test_long_never_moves_backward_even_if_best_price_regresses():
    # a stale/out-of-order best_price must not un-ratchet a stop already
    # moved further forward by an earlier, better reading
    new_stop = compute_trailed_stop(
        side="BUY", entry_price=100.0, best_price=101.0,
        trail_start_points=5.0, trail_step_points=1.0, current_stop=106.0)
    assert new_stop == 106.0


def test_short_mirrors_long_in_the_opposite_direction():
    new_stop = compute_trailed_stop(
        side="SELL", entry_price=100.0, best_price=93.0,
        trail_start_points=5.0, trail_step_points=1.0, current_stop=105.0)
    assert new_stop == 94.0  # 100 - (5 - 1 + floor(2/1)*1)


def test_short_never_moves_backward():
    new_stop = compute_trailed_stop(
        side="SELL", entry_price=100.0, best_price=99.0,
        trail_start_points=5.0, trail_step_points=1.0, current_stop=94.0)
    assert new_stop == 94.0
