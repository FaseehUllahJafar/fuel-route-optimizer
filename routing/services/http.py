"""Pooled HTTP session for the external services, with one retry on transient failures."""

from functools import cache

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


@cache
def http_session() -> requests.Session:
    retry = Retry(
        total=1,
        read=0,
        backoff_factor=0.5,
        status_forcelist=(429, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = settings.HTTP_USER_AGENT
    return session
