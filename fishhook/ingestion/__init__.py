"""Data ingestion layer - web scraping with request interception and dynamic value handling."""

from fishhook.ingestion.engine import ScrapingEngine
from fishhook.ingestion.interceptor import RequestInterceptor, InterceptedRequest
from fishhook.ingestion.dynamic_values import DynamicValueExtractor
from fishhook.ingestion.proxy_manager import ProxyManager

__all__ = [
    "ScrapingEngine",
    "RequestInterceptor",
    "InterceptedRequest",
    "DynamicValueExtractor",
    "ProxyManager",
]
