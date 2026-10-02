"""Retry rejected requests only, never replay a consumed stream or an agent run."""

import asyncio
import logging
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)


def provider_quota_exhausted(body):
    return any(code in body.lower() for code in (
        'insufficient_quota', 'billing_hard_limit', 'quota_exceeded',
        'generaterequestsperdayperproject', 'requests_per_day',
    ))


def retry_delay(retry_after, attempt, body, elapsed=0):
    if provider_quota_exhausted(body):
        return None
    delay = 3 * (2 ** attempt)
    if retry_after:
        try:
            delay = float(retry_after)
        except (TypeError, ValueError):
            try:
                date = parsedate_to_datetime(retry_after)
                delay = (date - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                pass
    if not math.isfinite(delay) or delay > 15 or elapsed + max(1, delay) > 25:
        return None
    return max(1, delay)


async def request_with_rate_limit_retry(session, **kwargs):
    waited = 0
    for attempt in range(3):
        response = await session.request(**kwargs)
        if response.status not in (429, 502, 503, 504) or attempt == 2:
            return response
        try:
            body = await response.text()
            delay = retry_delay(response.headers.get('Retry-After'), attempt, body, waited)
        except BaseException:
            response.release()
            raise
        if delay is None:
            return response
        response.release()
        log.warning('Provider rejected request (HTTP %s); retry %s/2 in %ss', response.status, attempt + 1, delay)
        await asyncio.sleep(delay)
        waited += delay
