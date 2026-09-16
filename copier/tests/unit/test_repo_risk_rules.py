"""Repo access for org_risk_rules and position_trailing_state. Uses the
same fixture/style already established for this file's other repo
tests -- a real Postgres connection against the test database, one test
per behavior, no mocking of the DB layer itself.
"""
import psycopg
import pytest

from copier.db.repo import Repo


@pytest.fixture
def repo(db):
    # `db` (copier/tests/conftest.py) is the DSN string this test package's
    # other repo tests already build a live-DB Repo from (see
    # test_repo_orgs.py's `seeded` fixture and test_repo_mt5.py's `world`
    # fixture) -- there is no separate `postgres_dsn` fixture in this repo.
    return Repo(db)


def test_load_risk_rule_returns_none_when_unconfigured(repo, db):
    assert repo.load_risk_rule(org_id=999, symbol="XAUUSD") is None


def test_load_risk_rule_returns_the_configured_row(repo, db):
    with psycopg.connect(db, autocommit=True) as conn:
        # org_risk_rules.org_id REFERENCES orgs(id); a fresh test database
        # has no orgs yet, so the row below needs a real org first. The
        # `db` fixture truncates orgs with RESTART IDENTITY before every
        # test, so this insert is guaranteed to land on id=1.
        conn.execute("INSERT INTO orgs (name) VALUES ('Org A')")
        conn.execute(
            "INSERT INTO org_risk_rules (org_id, symbol, stop_points, target_points, "
            "trailing_enabled, trail_start_points, trail_step_points) "
            "VALUES (1, 'XAUUSD', 50.0, 150.0, true, 10.0, 5.0)")
    rule = repo.load_risk_rule(org_id=1, symbol="XAUUSD")
    assert rule.stop_points == 50.0
    assert rule.target_points == 150.0
    assert rule.trailing_enabled is True
    assert rule.trail_start_points == 10.0
    assert rule.trail_step_points == 5.0


def test_upsert_then_load_trailing_state_round_trips(repo, db):
    repo.upsert_trailing_state(account_id=1, position_id=100,
                               best_price=4200.0, current_stop=4150.0)
    state = repo.load_trailing_state(account_id=1, position_id=100)
    assert state.best_price == 4200.0
    assert state.current_stop == 4150.0


def test_upsert_trailing_state_overwrites_on_conflict(repo, db):
    repo.upsert_trailing_state(account_id=1, position_id=100,
                               best_price=4200.0, current_stop=4150.0)
    repo.upsert_trailing_state(account_id=1, position_id=100,
                               best_price=4210.0, current_stop=4160.0)
    state = repo.load_trailing_state(account_id=1, position_id=100)
    assert state.best_price == 4210.0
    assert state.current_stop == 4160.0


def test_delete_trailing_state_removes_the_row(repo, db):
    repo.upsert_trailing_state(account_id=1, position_id=100,
                               best_price=4200.0, current_stop=4150.0)
    repo.delete_trailing_state(account_id=1, position_id=100)
    assert repo.load_trailing_state(account_id=1, position_id=100) is None


def test_load_all_trailing_state_rows_returns_every_tracked_position(repo, db):
    repo.upsert_trailing_state(account_id=1, position_id=100,
                               best_price=4200.0, current_stop=4150.0)
    repo.upsert_trailing_state(account_id=2, position_id=200,
                               best_price=1.09, current_stop=1.085)
    rows = repo.load_all_trailing_state_rows()
    assert {(r.account_id, r.position_id) for r in rows} == {(1, 100), (2, 200)}


def test_trailing_state_rows_carry_updated_at(repo, db):
    """The trailing loop's staleness check (main.py,
    TRAILING_STATE_STALE_AFTER_S) keys off this column -- it must round-trip
    through both load paths, not just exist in the schema."""
    repo.upsert_trailing_state(account_id=1, position_id=100,
                               best_price=4200.0, current_stop=4150.0)
    loaded = repo.load_trailing_state(account_id=1, position_id=100)
    assert loaded.updated_at is not None
    (from_all,) = repo.load_all_trailing_state_rows()
    assert from_all.updated_at == loaded.updated_at


def test_load_risk_rule_for_position_resolves_through_an_mt5_symbol_alias(
    repo, db, seed_mt5_account,
):
    """org_risk_rules.symbol is always canonical (e.g. "XAUUSD"), but an
    MT5 account with a broker-suffixed symbol alias configured (e.g.
    canonical "XAUUSD" -> broker "XAUUSDm") keeps the BROKER's name in
    positions.symbol -- see load_risk_rule_for_position's docstring for the
    full mechanism. A raw text-equality join would silently never find the
    rule for such a position; the join must resolve through symbol_aliases
    first."""
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('MT5 Alias Org') RETURNING id").fetchone()
        mt5_id = seed_mt5_account(org_id, role="master")
        conn.execute(
            "INSERT INTO org_risk_rules (org_id, symbol, stop_points, target_points, "
            "trailing_enabled, trail_start_points, trail_step_points) "
            "VALUES (%s, 'XAUUSD', 50.0, 150.0, true, 10.0, 5.0)", (org_id,))
        conn.execute(
            "INSERT INTO symbol_aliases (account_id, canonical, broker_name, source) "
            "VALUES (%s, 'XAUUSD', 'XAUUSDm', 'auto')", (mt5_id,))
        conn.execute(
            "INSERT INTO positions (account_id, position_id, org_id, symbol, side, "
            "volume, entry_price, status) "
            "VALUES (%s, 900, %s, 'XAUUSDm', 'BUY', 100000, 4200.0, 'open')",
            (mt5_id, org_id))

    rule = repo.load_risk_rule_for_position(account_id=mt5_id, position_id=900)

    assert rule is not None
    assert rule.symbol == "XAUUSD"
    assert rule.stop_points == 50.0
    assert rule.trailing_enabled is True


def test_load_risk_rule_for_position_matches_plain_symbol_with_no_alias_configured(
    repo, db, seed_mt5_account,
):
    """No symbol_aliases row at all (a cTrader account, or an MT5 symbol
    with no configured alias) must still match directly on positions.symbol
    -- the alias lookup is a fallback path, not the only path."""
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('No Alias Org') RETURNING id").fetchone()
        mt5_id = seed_mt5_account(org_id, role="master")
        conn.execute(
            "INSERT INTO org_risk_rules (org_id, symbol, stop_points, target_points, "
            "trailing_enabled, trail_start_points, trail_step_points) "
            "VALUES (%s, 'EURUSD', 20.0, 60.0, false, NULL, NULL)", (org_id,))
        conn.execute(
            "INSERT INTO positions (account_id, position_id, org_id, symbol, side, "
            "volume, entry_price, status) "
            "VALUES (%s, 901, %s, 'EURUSD', 'SELL', 100000, 1.09, 'open')",
            (mt5_id, org_id))

    rule = repo.load_risk_rule_for_position(account_id=mt5_id, position_id=901)

    assert rule is not None
    assert rule.symbol == "EURUSD"
    assert rule.stop_points == 20.0
