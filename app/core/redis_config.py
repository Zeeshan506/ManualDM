import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def redis_url_with_db(redis_url: str, db_index: int) -> str:
    parsed = urlparse(redis_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "db" in query:
        query["db"] = str(db_index)
        return urlunparse(parsed._replace(query=urlencode(query)))

    return urlunparse(parsed._replace(path=f"/{db_index}"))


def get_redis_base_url() -> str:
    return os.getenv("REDIS_CONNECTION_STRING") or "redis://127.0.0.1:6379"
