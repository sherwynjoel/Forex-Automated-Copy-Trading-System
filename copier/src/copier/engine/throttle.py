from collections import deque

from twisted.internet import defer


class TokenBucket:
    """FIFO token bucket. Default 40 req/s, burst 40 (server cap is 50 req/s).

    Watermark-based pacing: _last advances only by consumed increments (1/rate per token),
    never jumps to now. Pacing is bounded by elapsed-time budget: (now - _last) * rate,
    correct under any clock (fake Clock batch OR real stall).
    """

    def __init__(self, rate: float = 40.0, capacity: float = 40.0, clock=None):
        if clock is None:
            from twisted.internet import reactor as clock  # pragma: no cover
        self._clock = clock
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity  # Idle burst pool
        self._last = clock.seconds()  # Watermark: advances only by 1/rate per token released
        self._waiters: deque[defer.Deferred] = deque()
        self._pending_call = None

    def acquire(self) -> defer.Deferred:
        # Refill idle burst pool only when queue is empty
        if not self._waiters:
            now = self._clock.seconds()
            self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
            self._last = now

        # Grant from burst or queue
        if self._tokens >= 1 and not self._waiters:
            self._tokens -= 1
            return defer.succeed(None)

        # Queue and schedule drain
        def canceller(d):
            try:
                self._waiters.remove(d)
            except ValueError:
                pass

        d = defer.Deferred(canceller)
        self._waiters.append(d)
        self._schedule()
        return d

    def _schedule(self) -> None:
        if self._pending_call is not None and self._pending_call.active():
            return
        now = self._clock.seconds()
        # Time until next token is available: _last + 1/rate
        time_until_next = max(self._last + (1.0 / self._rate) - now, 0)
        self._pending_call = self._clock.callLater(time_until_next, self._drain)

    def _drain(self) -> None:
        self._pending_call = None
        now = self._clock.seconds()

        # Compute available budget from elapsed time since last release
        budget = (now - self._last) * self._rate

        # Release waiters FIFO, one per whole token, advancing watermark by 1/rate per release
        while self._waiters and budget >= 1:
            self._waiters.popleft().callback(None)
            self._last += 1.0 / self._rate
            budget -= 1

        # Reschedule if more waiters and budget exhausted
        if self._waiters:
            self._schedule()
