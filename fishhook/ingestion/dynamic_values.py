"""Dynamic value extractor - identifies and manages tokens, cookies, CSRF, etc."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fishhook.utils.logging import get_logger

logger = get_logger("ingestion.dynamic_values")


@dataclass
class DynamicValue:
    name: str
    value: str
    source_url: str
    extraction_method: str
    discovered_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None
    headers_used: list[str] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at

    def to_header_dict(self) -> dict[str, str]:
        return {h: self.value for h in self.headers_used}


class DynamicValueExtractor:
    CSRF_PATTERNS = [
        (r'name="csrf[_-]?token"\s+content="([^"]+)"', "meta_tag"),
        (r'"csrfToken"\s*:\s*"([^"]+)"', "json_field"),
        (r'"_csrf"\s*:\s*"([^"]+)"', "json_field"),
        (r'"csrf_token"\s*:\s*"([^"]+)"', "json_field"),
        (r'csrf[_-]?token["\s:=]+["\']?([a-zA-Z0-9+/=_-]{16,})["\']?', "regex"),
        (r'X-CSRF-TOKEN["\s:=]+["\']?([a-zA-Z0-9+/=_-]{16,})["\']?', "header"),
    ]

    SESSION_PATTERNS = [
        (r'"sessionId"\s*:\s*"([^"]+)"', "json_field"),
        (r'"session_id"\s*:\s*"([^"]+)"', "json_field"),
        (r'"sid"\s*:\s*"([^"]+)"', "json_field"),
    ]

    AUTH_PATTERNS = [
        (r'"accessToken"\s*:\s*"([^"]+)"', "json_field"),
        (r'"token"\s*:\s*"([^"]+)"', "json_field"),
        (r'"jwt"\s*:\s*"([^"]+)"', "json_field"),
        (r"Bearer\s+([a-zA-Z0-9._-]+)", "header"),
    ]

    COOKIE_PATTERNS = [
        (r'"__cf_bm"\s*=\s*([^;]+)', "cookie"),
        (r'"__stripe_mid"\s*=\s*([^;]+)', "cookie"),
        (r'"__cfduid"\s*=\s*([^;]+)', "cookie"),
    ]

    def __init__(self) -> None:
        self._values: dict[str, DynamicValue] = {}
        self._header_mapping: dict[str, str] = {}

    @property
    def values(self) -> dict[str, DynamicValue]:
        return {k: v for k, v in self._values.items() if not v.is_expired}

    @property
    def active_headers(self) -> dict[str, str]:
        headers = {}
        for dv in self._values.values():
            if not dv.is_expired:
                headers.update(dv.to_header_dict())
        return headers

    def extract_from_html(self, html: str, source_url: str) -> dict[str, DynamicValue]:
        found = {}
        for pattern, method in self.CSRF_PATTERNS:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                val = DynamicValue(
                    name="csrf_token",
                    value=match.group(1),
                    source_url=source_url,
                    extraction_method=method,
                    headers_used=["x-csrf-token", "x-xsrf-token"],
                )
                found["csrf_token"] = val
                self._values["csrf_token"] = val
                break

        for pattern, method in self.SESSION_PATTERNS:
            match = re.search(pattern, html)
            if match:
                val = DynamicValue(
                    name="session_id",
                    value=match.group(1),
                    source_url=source_url,
                    extraction_method=method,
                )
                found["session_id"] = val
                self._values["session_id"] = val
                break

        for pattern, method in self.AUTH_PATTERNS:
            match = re.search(pattern, html)
            if match:
                val = DynamicValue(
                    name="auth_token",
                    value=match.group(1),
                    source_url=source_url,
                    extraction_method=method,
                    headers_used=["Authorization"],
                )
                found["auth_token"] = val
                self._values["auth_token"] = val
                break

        if found:
            logger.info(f"Extracted {len(found)} dynamic values from {source_url}")
        return found

    def extract_from_headers(
        self, headers: dict[str, str], source_url: str
    ) -> dict[str, DynamicValue]:
        found = {}
        for name, value in headers.items():
            lower_name = name.lower()
            if lower_name in ("set-cookie",):
                continue
            if "csrf" in lower_name or "xsrf" in lower_name:
                dv = DynamicValue(
                    name=lower_name,
                    value=value,
                    source_url=source_url,
                    extraction_method="response_header",
                    headers_used=[lower_name],
                )
                found[lower_name] = dv
                self._values[lower_name] = dv
        return found

    def extract_from_json(
        self, data: dict[str, Any], source_url: str
    ) -> dict[str, DynamicValue]:
        found = {}
        token_keys = [
            "csrf_token",
            "csrfToken",
            "_csrf",
            "token",
            "accessToken",
            "session_id",
            "sessionId",
            "jwt",
        ]
        for key in token_keys:
            if key in data and isinstance(data[key], str):
                dv = DynamicValue(
                    name=key,
                    value=data[key],
                    source_url=source_url,
                    extraction_method="json_response",
                )
                found[key] = dv
                self._values[key] = dv
        return found

    def build_request_headers(
        self,
        base_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        headers = dict(base_headers or {})
        headers.update(self.active_headers)
        return headers

    def clear_expired(self) -> int:
        expired = [k for k, v in self._values.items() if v.is_expired]
        for k in expired:
            del self._values[k]
        return len(expired)

    def clear_all(self) -> None:
        self._values.clear()
