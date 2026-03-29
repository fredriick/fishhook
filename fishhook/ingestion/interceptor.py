"""Network request interceptor - captures and reverse-engineers HTTP traffic."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from fishhook.utils.logging import get_logger

logger = get_logger("ingestion.interceptor")


@dataclass
class InterceptedRequest:
    url: str
    method: str
    headers: dict[str, str]
    post_data: str | None
    request_id: str
    resource_type: str
    is_navigation: bool
    response_status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    response_body: str | None = None
    timing_ms: float | None = None

    @property
    def is_api_call(self) -> bool:
        api_patterns = [
            "/api/",
            "/graphql",
            "/v1/",
            "/v2/",
            "/v3/",
            ".json",
            "/query",
            "/search",
            "/fetch",
        ]
        return any(p in self.url.lower() for p in api_patterns)

    @property
    def is_xhr(self) -> bool:
        return self.resource_type in ("xhr", "fetch")

    def to_replay_template(self) -> dict[str, Any]:
        template = {
            "url": self.url,
            "method": self.method,
            "headers": dict(self.headers),
            "resource_type": self.resource_type,
        }
        if self.post_data:
            template["body"] = self.post_data
        return template

    def extract_dynamic_tokens(self) -> dict[str, str]:
        tokens = {}
        if self.post_data:
            csrf_patterns = [
                r'"csrf[_-]?token"\s*:\s*"([^"]+)"',
                r'"_token"\s*:\s*"([^"]+)"',
                r'"authenticity_token"\s*:\s*"([^"]+)"',
            ]
            for pattern in csrf_patterns:
                match = re.search(pattern, self.post_data)
                if match:
                    tokens["csrf_token"] = match.group(1)

        for header_name in ("x-csrf-token", "x-xsrf-token", "x-requested-with"):
            if header_name in {k.lower() for k in self.headers}:
                val = self.headers.get(header_name, "")
                if val:
                    tokens[header_name] = val

        return tokens


class RequestInterceptor:
    def __init__(self) -> None:
        self._captured: list[InterceptedRequest] = []
        self._response_bodies: dict[str, str] = {}
        self._dynamic_tokens: dict[str, str] = {}

    @property
    def captured_requests(self) -> list[InterceptedRequest]:
        return list(self._captured)

    @property
    def api_requests(self) -> list[InterceptedRequest]:
        return [r for r in self._captured if r.is_api_call or r.is_xhr]

    @property
    def dynamic_tokens(self) -> dict[str, str]:
        return dict(self._dynamic_tokens)

    async def on_request(self, request: Any) -> None:
        try:
            entry = InterceptedRequest(
                url=request.url,
                method=request.method,
                headers=dict(request.headers),
                post_data=request.post_data,
                request_id=id(request),
                resource_type=request.resource_type,
                is_navigation=request.is_navigation_request(),
            )
            self._captured.append(entry)
            logger.debug(f"Intercepted: {request.method} {request.url}")
        except Exception as e:
            logger.warning(f"Failed to intercept request: {e}")

    async def on_response(self, response: Any) -> None:
        try:
            url = response.url
            status = response.status
            for entry in reversed(self._captured):
                if entry.url == url and entry.response_status is None:
                    entry.response_status = status
                    entry.response_headers = dict(response.headers)
                    try:
                        body = await response.text()
                        entry.response_body = body
                        self._response_bodies[url] = body
                    except Exception:
                        pass
                    break

            tokens = self._extract_tokens_from_response(response)
            self._dynamic_tokens.update(tokens)
        except Exception as e:
            logger.warning(f"Failed to process response: {e}")

    def _extract_tokens_from_response(self, response: Any) -> dict[str, str]:
        tokens = {}
        url = response.url
        body = self._response_bodies.get(url, "")

        csrf_patterns = [
            r'name="csrf[_-]?token"\s+content="([^"]+)"',
            r'"csrfToken"\s*:\s*"([^"]+)"',
            r'"_csrf"\s*:\s*"([^"]+)"',
        ]
        for pattern in csrf_patterns:
            match = re.search(pattern, body)
            if match:
                tokens["csrf_token"] = match.group(1)

        session_patterns = [
            r'"session[_-]?id"\s*:\s*"([^"]+)"',
            r'"sessionId"\s*:\s*"([^"]+)"',
        ]
        for pattern in session_patterns:
            match = re.search(pattern, body)
            if match:
                tokens["session_id"] = match.group(1)

        return tokens

    def get_replayable_requests(self) -> list[dict[str, Any]]:
        replayable = []
        for req in self._captured:
            if req.is_api_call or req.is_xhr:
                template = req.to_replay_template()
                tokens = req.extract_dynamic_tokens()
                if tokens:
                    template["dynamic_tokens"] = tokens
                replayable.append(template)
        return replayable

    def clear(self) -> None:
        self._captured.clear()
        self._response_bodies.clear()

    def summary(self) -> dict[str, Any]:
        total = len(self._captured)
        api_count = len(self.api_requests)
        methods = {}
        for req in self._captured:
            methods[req.method] = methods.get(req.method, 0) + 1
        return {
            "total_requests": total,
            "api_requests": api_count,
            "methods": methods,
            "dynamic_tokens_found": len(self._dynamic_tokens),
            "replayable_endpoints": len(self.get_replayable_requests()),
        }
