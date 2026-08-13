from twisted.internet.task import Clock

from copier.engine.throttle import TokenBucket


def fired(d):
    out = []
    d.addCallback(out.append)
    return out


def test_first_40_are_immediate():
    bucket = TokenBucket(clock=Clock())
    results = [fired(bucket.acquire()) for _ in range(40)]
    assert all(results)


def test_41st_waits_for_refill():
    clock = Clock()
    bucket = TokenBucket(clock=clock)
    for _ in range(40):
        bucket.acquire()
    out = fired(bucket.acquire())
    assert not out
    clock.advance(0.026)          # one token refills in 1/40 s
    assert out


def test_49_slave_fanout_completes_in_well_under_2s():
    # spec §5: 49-slave fan-out ~1.2 s worst case at 40 req/s
    clock = Clock()
    bucket = TokenBucket(clock=clock)
    outs = [fired(bucket.acquire()) for _ in range(89)]   # 40 burst + 49 queued
    clock.advance(1.3)
    assert all(outs)


def test_fifo_order():
    clock = Clock()
    bucket = TokenBucket(rate=1, capacity=1, clock=clock)
    bucket.acquire()
    order = []
    bucket.acquire().addCallback(lambda _: order.append("first"))
    bucket.acquire().addCallback(lambda _: order.append("second"))
    clock.advance(2.5)
    assert order == ["first", "second"]


def test_pacing_with_partial_advance():
    # Verify pacing is enforced: partial time advance should release only partial backlog
    clock = Clock()
    bucket = TokenBucket(clock=clock)
    outs = [fired(bucket.acquire()) for _ in range(89)]  # 40 burst + 49 queued

    # Advance only 0.5s: should have ~40 + 0.5*40 = 60 tokens released
    clock.advance(0.5)
    fired_count = sum(1 for out in outs if out)
    assert 55 <= fired_count <= 65, f"Expected ~60 fired at t=0.5, got {fired_count}"
    assert not all(outs), "Not all should be fired yet"

    # Advance the remaining 1.3s to complete: now all should be done
    clock.advance(0.8)
    assert all(outs), "All should be fired by t=1.3"
