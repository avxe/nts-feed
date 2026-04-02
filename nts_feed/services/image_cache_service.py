import os
import hashlib
import logging
from pathlib import Path
from typing import Optional, Tuple

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from ..runtime_paths import image_cache_dir

logger = logging.getLogger(__name__)


class ImageCacheService:
    """
    File-based image cache for remote thumbnails.

    - Caches by MD5 of the source URL
    - Validates content type is image/*
    - Negative cache for failed fetches (avoids repeated retries)
    - Optional simple host allowlist via IMAGE_CACHE_ALLOWED_HOSTS (comma-separated)
    """

    # How long to remember a failed fetch before retrying (seconds)
    NEGATIVE_CACHE_TTL = 3600  # 1 hour

    def __init__(self, cache_dir: str | None = None):
        # Resolve to absolute path to ensure Flask send_file works correctly
        self.cache_dir = Path(cache_dir or image_cache_dir()).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Hosts allowed to be proxied; if empty, allow all http/https hosts
        env_hosts = os.getenv("IMAGE_CACHE_ALLOWED_HOSTS", "").strip()
        self.allowed_hosts = {h.strip().lower() for h in env_hosts.split(",") if h.strip()} or set()

        # When false (default), allow any http/https host. When true, restrict to allowed_hosts.
        self.strict_allowed_hosts = os.getenv("IMAGE_CACHE_STRICT_ALLOWED_HOSTS", "false").strip().lower() == "true"

        # Request headers: user-agent and referer to reduce remote blocks
        self.user_agent = os.getenv("IMAGE_CACHE_USER_AGENT", "Mozilla/5.0 (NTSFeed)")
        self.default_referer = os.getenv("IMAGE_CACHE_REFERER", "")

        # Negative cache: track URLs that failed to avoid repeated retries
        # Maps URL hash -> timestamp of failure
        self._negative_cache: dict[str, float] = {}

    @staticmethod
    def _hash_url(url: str) -> str:
        return hashlib.md5(url.encode("utf-8")).hexdigest()

    @staticmethod
    def _infer_extension(content_type: Optional[str], url_path: str) -> str:
        # Prefer content-type; fall back to URL path extension; default to .jpg
        if content_type:
            ctype = content_type.split(";")[0].strip().lower()
            if ctype == "image/jpeg" or ctype == "image/jpg":
                return ".jpg"
            if ctype == "image/png":
                return ".png"
            if ctype == "image/webp":
                return ".webp"
            if ctype == "image/gif":
                return ".gif"
            if ctype.startswith("image/"):
                return ".img"
        # Fallback by URL path
        ext = Path(url_path).suffix.lower()
        return ext if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"

    def _validate_host(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                return False
            host = (parsed.hostname or "").lower()
            if not host:
                return False
            if not self.strict_allowed_hosts or not self.allowed_hosts:
                # If not strict or no explicit allowlist, allow http/https hosts
                return parsed.scheme in {"http", "https"}
            # Allow if host matches any suffix in allowed_hosts
            return any(host == ah or host.endswith("." + ah) for ah in self.allowed_hosts)
        except Exception:
            return False

    def _build_paths(self, url: str, content_type_hint: Optional[str] = None) -> Tuple[Path, Path]:
        key = self._hash_url(url)
        parsed = urlparse(url)
        ext = self._infer_extension(content_type_hint, parsed.path)
        final_path = self.cache_dir / f"{key}{ext}"
        tmp_path = self.cache_dir / f"{key}.tmp"
        return final_path, tmp_path

    def get_cached_path(self, url: str) -> Optional[Path]:
        """Return path if cached, else None."""
        # We do not know extension a priori; try common ones
        key = self._hash_url(url)
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".img"):
            p = self.cache_dir / f"{key}{ext}"
            if p.exists() and p.stat().st_size > 0:
                return p
        return None

    def fetch_and_cache(self, url: str, timeout_seconds: int = 15) -> Optional[Path]:
        if not self._validate_host(url):
            logger.warning("ImageCacheService: blocked host for URL: %s", url)
            return None

        try:
            # Compose headers
            headers = {}
            if self.user_agent:
                headers["User-Agent"] = self.user_agent
            try:
                parsed = urlparse(url)
                host = (parsed.hostname or "").lower()
                if self.default_referer:
                    headers["Referer"] = self.default_referer
                elif host.endswith("ntslive.co.uk") or host.endswith("nts.live"):
                    headers["Referer"] = "https://www.nts.live/"
                elif parsed.scheme and host:
                    headers["Referer"] = f"{parsed.scheme}://{host}/"
            except Exception:
                pass

            resp = requests.get(url, timeout=timeout_seconds, stream=True, headers=headers)
            resp.raise_for_status()
            content_type = (resp.headers.get("Content-Type") or "").lower()
            # Accept if content-type is image/* OR if URL clearly indicates image extension
            url_ext = Path(urlparse(url).path).suffix.lower()
            looks_like_image = url_ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
            if not (content_type.startswith("image/") or looks_like_image):
                logger.warning("ImageCacheService: unlikely image (ctype=%s, url_ext=%s) for %s", content_type, url_ext, url)
                return None

            final_path, tmp_path = self._build_paths(url, content_type)
            # Stream to tmp then rename atomically
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            os.replace(tmp_path, final_path)
            return final_path
        except Exception as e:
            try:
                if 'tmp_path' in locals() and tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except Exception:
                pass
            logger.error("ImageCacheService: failed to fetch %s: %s", url, e)
            return None

    def _is_negatively_cached(self, url: str) -> bool:
        """Check if a URL recently failed and should not be retried yet."""
        import time
        key = self._hash_url(url)
        failed_at = self._negative_cache.get(key)
        if failed_at is None:
            return False
        if time.time() - failed_at > self.NEGATIVE_CACHE_TTL:
            del self._negative_cache[key]
            return False
        return True

    def _mark_negative(self, url: str) -> None:
        """Record that a fetch failed so we skip retries for a while."""
        import time
        self._negative_cache[self._hash_url(url)] = time.time()

    def get_or_fetch(self, url: str) -> Optional[Path]:
        cached = self.get_cached_path(url)
        if cached:
            return cached
        if self._is_negatively_cached(url):
            return None
        result = self.fetch_and_cache(url)
        if result is None:
            self._mark_negative(url)
        return result

    def prefetch_many(self, urls, concurrency: int = 8, force: bool = False):
        """
        Prefetch and cache many image URLs concurrently.

        Returns dict stats: { total, fetched, skipped, errors }.
        """
        try:
            unique = []
            seen = set()
            for u in urls or []:
                if not u:
                    continue
                s = str(u).strip()
                if not s:
                    continue
                if s in seen:
                    continue
                seen.add(s)
                unique.append(s)

            stats = { 'total': len(unique), 'fetched': 0, 'skipped': 0, 'errors': 0 }

            if not unique:
                return stats

            max_workers = max(1, int(concurrency or 1))

            def worker(u: str):
                try:
                    if not force:
                        cached = self.get_cached_path(u)
                        if cached:
                            return 'skipped'
                    return 'fetched' if self.fetch_and_cache(u) else 'error'
                except Exception:
                    return 'error'

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [pool.submit(worker, u) for u in unique]
                for fut in as_completed(futures):
                    try:
                        result = fut.result()
                        if result == 'fetched':
                            stats['fetched'] += 1
                        elif result == 'skipped':
                            stats['skipped'] += 1
                        else:
                            stats['errors'] += 1
                    except Exception:
                        stats['errors'] += 1

            return stats
        except Exception as e:
            logger.error("ImageCacheService.prefetch_many failed: %s", e)
            return { 'total': 0, 'fetched': 0, 'skipped': 0, 'errors': 1 }

