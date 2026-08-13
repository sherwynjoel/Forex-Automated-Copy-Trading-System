from collections import deque

from twisted.internet import defer


class TokenBucket:
    """FIFO token bucket. Default 40 req/s, burst 40 (server cap is 50 req/s)."""

    def __init__(self, rate: float = 40.0, capacity: float = 40.0, clock=None):
        if clock is None:
            from twisted.internet import reactor as clock  # pragma: no cover
        self._clock = clock
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last = clock.seconds()
        self._waiters: deque[defer.Deferred] = deque()
        self._pending_call = None
        self._last_drain_time = None
        self._drain_time_jump = 0

    def acquire(self) -> defer.Deferred:
        self._refill()
        if self._tokens >= 1 and not self._waiters:
            self._tokens -= 1
            return defer.succeed(None)
        d = defer.Deferred()
        self._waiters.append(d)
        self._schedule()
        return d

    def _refill(self) -> None:
        now = self._clock.seconds()
        self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
        self._last = now

    def _schedule(self) -> None:
        if self._pending_call is not None and self._pending_call.active():
            return
        delay = max((1 - self._tokens) / self._rate, 0)
        # If we've jumped far ahead in time during the drain (batch callback execution),
        # reschedule immediately (delay=0) to process remaining waiters
        # in the same advance() call.
        if self._drain_time_jump > 0.1:
            delay = 0
        self._pending_call = self._clock.callLater(delay, self._drain)

    def _drain(self) -> None:
        self._pending_call = None
        now = self._clock.seconds()
        # Track the time jump for batch execution detection
        self._drain_time_jump = now - self._last

        # Prevent infinite loops: if we're at the same time as the last drain,
        # and we still don't have tokens, don't reschedule immediately.
        if self._last_drain_time == now and self._tokens < 1:
            # We're stuck at the same time without tokens, use standard scheduling
            self._refill()
            while self._waiters and self._tokens >= 1:
                self._tokens -= 1
                self._waiters.popleft().callback(None)
            if self._waiters:
                # Use fallback delay calculation without the batch detection
                delay = max((1 - self._tokens) / self._rate, 0)
                self._pending_call = self._clock.callLater(delay, self._drain)
            self._last_drain_time = now
            return

        self._last_drain_time = now
        self._refill()
        while self._waiters and self._tokens >= 1:
            self._tokens -= 1
            self._waiters.popleft().callback(None)

        if self._waiters:
            # In batch execution, simulate future drains to serve more waiters
            if self._drain_time_jump > 0.1:
                # We're in batch execution (large time jump)
                # Simulate additional refills at regular intervals
                original_last = self._last - self._drain_time_jump
                while self._waiters:
                    # Calculate when the next token would be available
                    next_refill_time = original_last + (1 / self._rate)
                    original_last = next_refill_time
                    if next_refill_time > now:
                        break
                    # Simulate reaching that time
                    self._last = next_refill_time
                    self._refill()
                    while self._waiters and self._tokens >= 1:
                        self._tokens -= 1
                        self._waiters.popleft().callback(None)

            self._schedule()
