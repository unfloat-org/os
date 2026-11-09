"""JSON over HTTPS with the standard library, shared by the channel adapters."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping

TIMEOUT_S = 30


class HttpError(Exception):
    """The server answered with an error status."""

    def __init__(self, method: str, url: str, status: int, detail: str):
        self.method, self.url, self.status, self.detail = method, url, status, detail
        super().__init__(f"{method} {url.split('?')[0]}: HTTP {status}: {detail}")


class NetworkError(Exception):
    """No answer: the request may or may not have reached the server."""


def send(
    method: str,
    url: str,
    *,
    data: Mapping | list | None = None,
    form: bool = False,
    headers: Mapping[str, str] | None = None,
    bearer: str | None = None,
    redact: Iterable[str] = (),
    detail_chars: int = 500,
    timeout: float = TIMEOUT_S,
) -> dict | list:
    """One request; the decoded JSON answer ({} when empty). Secrets in `redact` never appear in errors."""
    all_headers = dict(headers or {})
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            all_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        else:
            body = json.dumps(data).encode("utf-8")
            all_headers.setdefault("Content-Type", "application/json")
    if bearer:
        all_headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, data=body, method=method, headers=all_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        for secret in redact:
            if secret:
                detail = detail.replace(secret, "[redacted]")
        raise HttpError(method, url, exc.code, detail[:detail_chars]) from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise NetworkError(f"{method} {url.split('?')[0]}: network failure ({exc})") from exc
