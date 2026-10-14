"""One HTTP request with bounded response bytes and elapsed time."""

import json
import time
from contextlib import closing

import requests

MAX_BYTES = 4_000_000


def get_json(url, *, params, headers, timeout):
    started = time.monotonic()
    with closing(
        requests.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            stream=True,
            allow_redirects=False,
        )
    ) as response:
        response.raise_for_status()
        chunks = []
        size = 0
        for chunk in response.iter_content(chunk_size=16_384):
            size += len(chunk)
            if size > MAX_BYTES or time.monotonic() - started > 45:
                raise requests.RequestException(
                    "Upstream response exceeded resource limits"
                )
            chunks.append(chunk)
        try:
            return json.loads(b"".join(chunks))
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise requests.RequestException("Invalid upstream JSON") from exc
