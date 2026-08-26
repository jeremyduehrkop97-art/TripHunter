"""A very small local JSON file cache with a TTL.

No external services (no Redis, no database) - MVP 0.2 only needs to avoid
duplicate calls to a paid/rate-limited API for the same search within a
short time window. Each cache entry is one JSON file under the given cache
directory, named after a hash of its key. Entries older than the configured
TTL are treated as misses and get overwritten on the next write.

Cache files live under data/cache/ by default and are git-ignored - they
are disposable, rebuildable local state, not project data.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = Path("data/cache")
DEFAULT_TTL_SECONDS = 24 * 60 * 60


class FileCache:
    def __init__(
        self,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._ttl_seconds = ttl_seconds

    def get(self, key: str) -> Any | None:
        path = self._path_for(key)
        if not path.is_file():
            return None

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        stored_at = raw.get("stored_at")
        if stored_at is None or time.time() - stored_at > self._ttl_seconds:
            return None

        return raw.get("value")

    def set(self, key: str, value: Any) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(key)
        payload = {"stored_at": time.time(), "value": value}
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _path_for(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{digest}.json"


def flight_search_cache_key(
    origin: str, destination: str, departure_date: str, return_date: str
) -> str:
    return f"flight:{origin}:{destination}:{departure_date}:{return_date}"
