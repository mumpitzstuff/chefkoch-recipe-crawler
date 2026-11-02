from twisted.internet import defer, reactor
from scrapy.downloadermiddlewares.retry import RetryMiddleware
from scrapy.utils.response import response_status_message


class TooManyRequestsRetryMiddleware(RetryMiddleware):
    """
    Modifies RetryMiddleware to delay retries on status 429.

    On a 429 response the entire engine is paused (because a 429 from
    chefkoch.de means the IP is blocked domain-wide, so continuing to
    send requests is pointless).  Multiple concurrent 429 responses are
    coalesced into a single pause/unpause cycle: the first one starts the
    timer; any subsequent ones that arrive while the engine is already
    paused simply extend the timer if their Retry-After is longer, or
    wait silently if it is shorter.
    """

    DEFAULT_DELAY = 300  # seconds
    MAX_DELAY = 600      # cap absurd Retry-After values

    def __init__(self, settings):
        super().__init__(settings)
        self._pause_active = False   # True while the engine is paused for 429
        self._waiters = []           # Deferreds waiting for the engine to resume
        self._delayed_call = None    # The active reactor timer (IDelayedCall)

    async def process_response(self, request, response, spider):
        if request.meta.get('dont_retry', False):
            return response

        if response.status in self.retry_http_codes:
            if response.status == 429:
                delay = self._parse_delay(response)

                if not self._pause_active:
                    # First 429 in this wave: pause the engine and start timer.
                    self._pause_active = True
                    spider.crawler.engine.pause()
                    spider.logger.info(
                        'Got 429 for %s — pausing engine for %s seconds.', request, delay
                    )
                    self._delayed_call = reactor.callLater(
                        delay, self._unpause, spider
                    )
                else:
                    # Engine is already paused — extend timer if this 429 needs meaningfully longer.
                    remaining = self._delayed_call.getTime() - reactor.seconds()
                    if delay > remaining + 1:  # +1s tolerance avoids spurious resets when delays are equal
                        self._delayed_call.reset(delay)
                        spider.logger.info(
                            'Got 429 for %s with longer Retry-After — extending pause to %s seconds.',
                            request, delay
                        )
                    else:
                        spider.logger.debug(
                            'Got 429 for %s — engine already paused, waiting for resume.', request
                        )

                # All 429 responses wait here until the engine is unpaused.
                d = defer.Deferred()
                self._waiters.append(d)
                await d

            reason = response_status_message(response.status)
            return self._retry(request, reason, spider) or response

        return response

    def _parse_delay(self, response):
        """Read Retry-After header; fall back to DEFAULT_DELAY."""
        delay = self.DEFAULT_DELAY
        retry_after = response.headers.get('retry-after')
        try:
            retry_after = int(retry_after)
        except (ValueError, TypeError):
            pass
        else:
            if retry_after > 0:
                delay = min(self.MAX_DELAY, retry_after)
        return delay

    def _unpause(self, spider):
        """Called by reactor.callLater when the backoff period expires."""
        self._pause_active = False
        self._delayed_call = None
        spider.crawler.engine.unpause()
        spider.logger.info('Engine resumed after 429 backoff.')

        # Wake up all coroutines that were waiting on this pause.
        waiters, self._waiters = self._waiters, []
        for d in waiters:
            d.callback(None)
