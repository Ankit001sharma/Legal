"""Domain-agnostic validation heuristics."""

from __future__ import annotations

import re
from urllib.parse import urlparse


class GenericDomainAdapter:
    """Default authority and reliability scoring for general research."""

    _HIGH_AUTHORITY_SUFFIXES = (".gov", ".edu", ".ac.uk", ".org")
    _LOW_AUTHORITY_MARKERS = ("blog.", "medium.com", "wordpress.com", "reddit.com")

    def authority_score(self, url: str, title: str, fetched: bool) -> int:
        host = urlparse(url or "").netloc.lower()
        if not host:
            return 30
        if any(host.endswith(s) or s in host for s in (".gov", ".edu")):
            return 90 if fetched else 70
        if host.endswith(".org"):
            return 75 if fetched else 55
        if any(marker in host for marker in self._LOW_AUTHORITY_MARKERS):
            return 35 if fetched else 20
        return 60 if fetched else 40

    def reliability_score(self, fetched: bool, access_denied: bool, excerpt: str) -> int:
        if access_denied:
            return 10
        if fetched and len((excerpt or "").strip()) >= 200:
            return 90
        if fetched:
            return 70
        return 40

    def freshness_score(self, title: str, excerpt: str) -> int:
        text = f"{title} {excerpt}"
        years = [int(y) for y in re.findall(r"\b(20[0-2]\d)\b", text)]
        if not years:
            return 50
        latest = max(years)
        from datetime import datetime

        age = datetime.now().year - latest
        if age <= 1:
            return 95
        if age <= 3:
            return 80
        if age <= 5:
            return 65
        if age <= 10:
            return 45
        return 25

    def extract_keywords(self, text: str) -> set[str]:
        words = re.findall(r"[a-zA-Z]{4,}", (text or "").lower())
        stop = {
            "that",
            "this",
            "with",
            "from",
            "have",
            "been",
            "were",
            "will",
            "would",
            "could",
            "should",
            "about",
            "their",
            "which",
            "there",
            "these",
            "those",
            "research",
            "legal",
            "india",
            "indian",
        }
        return {w for w in words if w not in stop}
