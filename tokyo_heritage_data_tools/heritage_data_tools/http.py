from __future__ import annotations

import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DEFAULT_UA = "TokyoHeritageDataTools/0.1 (+local research workflow)"


def session(retries: int = 3, backoff: float = 1.0, user_agent: str = DEFAULT_UA) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({
        "User-Agent": user_agent,
        "Accept": "*/*",
    })
    return s
