# rate_limiter.py
import logging
import time
import threading
from collections import defaultdict, deque
from typing import Dict, Optional
import requests
import streamlit as st

logger = logging.getLogger(__name__)

# Only transient network failures are worth retrying. Data problems (bad symbol,
# missing element, malformed response) will fail identically every time.
RETRYABLE_EXCEPTIONS = (requests.RequestException, ConnectionError, TimeoutError)


def _on_script_thread() -> bool:
    """True when Streamlit UI calls are safe (i.e. not inside a worker thread)."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx(suppress_warning=True) is not None
    except Exception:
        return False


class RateLimiter:
    """Thread-safe rate limiter for API calls"""

    def __init__(self):
        self._locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._call_times: Dict[str, deque] = defaultdict(deque)
        self._last_call: Dict[str, float] = defaultdict(float)

    def wait_if_needed(self, service: str, min_delay: float = 1.0,
                       calls_per_minute: int = 60) -> None:
        """
        Wait if needed to respect rate limits

        Args:
            service: Name of the service (e.g., 'yfinance', 'screener')
            min_delay: Minimum delay between calls in seconds
            calls_per_minute: Maximum calls per minute
        """
        with self._locks[service]:
            current_time = time.time()

            # Check minimum delay between calls
            last_call = self._last_call[service]
            if last_call > 0:
                time_since_last = current_time - last_call
                if time_since_last < min_delay:
                    sleep_time = min_delay - time_since_last
                    time.sleep(sleep_time)
                    current_time = time.time()

            # Check calls per minute limit
            call_times = self._call_times[service]
            # Remove calls older than 1 minute
            minute_ago = current_time - 60
            while call_times and call_times[0] < minute_ago:
                call_times.popleft()

            # If we've hit the rate limit, wait
            if len(call_times) >= calls_per_minute:
                wait_time = 60 - (current_time - call_times[0])
                if wait_time > 0:
                    time.sleep(wait_time)
                    current_time = time.time()

            # Record this call
            call_times.append(current_time)
            self._last_call[service] = current_time


class RetryHandler:
    """Handle retries with exponential backoff"""

    @staticmethod
    def retry_with_backoff(func, max_retries: int = 3, base_delay: float = 1.0,
                           service_name: str = "API"):
        """
        Retry function with exponential backoff

        Args:
            func: Function to retry
            max_retries: Maximum number of retries
            base_delay: Base delay for exponential backoff
            service_name: Name for error messages
        """
        for attempt in range(max_retries + 1):
            try:
                return func()
            except RETRYABLE_EXCEPTIONS as e:
                if attempt == max_retries:
                    # Caller's ErrorBoundary reports to the user; just log here
                    logger.error(f"{service_name} failed after {max_retries} retries: {e}")
                    raise

                delay = base_delay * (2 ** attempt)
                logger.warning(f"{service_name} attempt {attempt + 1} failed ({e}). Retrying in {delay}s")
                if _on_script_thread():
                    st.toast(f"{service_name}: network error, retrying in {delay:.0f}s…", icon="🔁")
                time.sleep(delay)


# Global rate limiter instance
rate_limiter = RateLimiter()


def rate_limited_call(service: str, func, min_delay: float = 1.0,
                      calls_per_minute: int = 60, max_retries: int = 3):
    """
    Make a rate-limited API call with retries

    Args:
        service: Service name for rate limiting
        func: Function to call
        min_delay: Minimum delay between calls
        calls_per_minute: Max calls per minute
        max_retries: Maximum retries
    """

    def wrapped_func():
        rate_limiter.wait_if_needed(service, min_delay, calls_per_minute)
        return func()

    return RetryHandler.retry_with_backoff(
        wrapped_func, max_retries, min_delay, service
    )