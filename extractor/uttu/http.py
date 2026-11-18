"""HTTP client with retries, rate limiting and useful error messages."""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

import requests

from . import config

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """A remote resource could not be fetched."""


class NotFound(FetchError):
    """The remote resource does not exist (404/410)."""


class HttpClient:
    def __init__(
        self,
        *,
        base_url: str = config.BASE_URL,
        timeout: tuple[float, float] = config.HTTP_TIMEOUT,
        retries: int = config.HTTP_RETRIES,
        min_interval: float = config.MIN_REQUEST_INTERVAL,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
            }
        )

    def absolute(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return self.base_url + ("" if url.startswith("/") else "/") + url

    def _throttle(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def get(self, url: str, *, allow_redirects: bool = True) -> requests.Response:
        full = self.absolute(url)
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                response = self._session.get(
                    full,
                    timeout=self.timeout,
                    allow_redirects=allow_redirects,
                )
            except requests.RequestException as exc:
                last_error = exc
                log.warning("GET %s failed (%s/%s): %s", full, attempt, self.retries, exc)
                self._sleep_before_retry(attempt, None)
                continue

            if response.status_code in (404, 410):
                raise NotFound(f"{response.status_code} for {full}")
            if response.status_code in (429, 500, 502, 503, 504, 522, 524):
                last_error = FetchError(f"HTTP {response.status_code} for {full}")
                log.warning(
                    "GET %s returned %s (%s/%s)",
                    full,
                    response.status_code,
                    attempt,
                    self.retries,
                )
                self._sleep_before_retry(attempt, response)
                continue
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise FetchError(f"HTTP {response.status_code} for {full}") from exc
            return response

        raise FetchError(f"GET {full} failed after {self.retries} attempts: {last_error}")

    def get_text(self, url: str, *, allow_redirects: bool = True) -> str:
        response = self.get(url, allow_redirects=allow_redirects)
        # The site consistently serves UTF-8. If a proxy omits the header,
        # requests can guess wrong and mangle Chinese, so force it.
        if not response.encoding or response.encoding.lower() in ("iso-8859-1", "ascii"):
            response.encoding = "utf-8"
        return response.text

    def post_json(self, url: str, payload: Any) -> requests.Response:
        full = self.absolute(url)
        self._throttle()
        return self._session.post(full, json=payload, timeout=self.timeout)

    @staticmethod
    def _sleep_before_retry(attempt: int, response: requests.Response | None) -> None:
        if response is not None and response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    time.sleep(min(float(retry_after), 30.0))
                    return
                except ValueError:
                    pass
        time.sleep(min(0.6 * (2 ** (attempt - 1)) + random.random() * 0.25, 8.0))


client = HttpClient()
