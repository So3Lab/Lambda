"""Web fetch tool."""

from __future__ import annotations

import html
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MAX_BYTES = 1_000_000
MAX_OUTPUT_CHARS = 20_000
_TEXT_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/rss+xml",
    "application/atom+xml",
)


def _is_text_content(content_type: str) -> bool:
    lower = content_type.lower()
    return any(lower.startswith(prefix) for prefix in _TEXT_CONTENT_TYPES)


def _charset_from_content_type(content_type: str) -> str:
    match = re.search(r"charset=([^;]+)", content_type, re.IGNORECASE)
    return match.group(1).strip(' "') if match else "utf-8"


def _html_to_text(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article|header|footer)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


async def web_fetch(
    url: str,
    timeout: int = 20,
    max_chars: int = MAX_OUTPUT_CHARS,
) -> dict:
    """Fetch a URL and return readable text.

    Args:
        url: HTTP or HTTPS URL to fetch.
        timeout: Max seconds before giving up.
        max_chars: Max response text chars to return.

    Returns:
        Dict with success, status_code, final_url, content_type, text, truncated, error.
    """
    return _web_fetch_sync(url=url, timeout=timeout, max_chars=max_chars)


def _web_fetch_sync(
    url: str,
    timeout: int = 20,
    max_chars: int = MAX_OUTPUT_CHARS,
) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {
            "success": False,
            "status_code": 0,
            "final_url": url,
            "content_type": "",
            "text": "",
            "truncated": False,
            "error": "Only absolute http:// or https:// URLs are supported.",
        }

    safe_timeout = max(1, min(timeout, 120))
    safe_max_chars = max(1, min(max_chars, MAX_OUTPUT_CHARS))
    request = Request(
        url,
        headers={
            "User-Agent": "LambdaCodingAgent/0.1 (+https://github.com/)",
            "Accept": "text/html,application/xhtml+xml,application/xml,application/json,text/plain;q=0.9,*/*;q=0.1",
        },
    )

    try:
        with urlopen(request, timeout=safe_timeout) as response:
            status_code = getattr(response, "status", 200)
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
            if content_type and not _is_text_content(content_type):
                return {
                    "success": False,
                    "status_code": status_code,
                    "final_url": final_url,
                    "content_type": content_type,
                    "text": "",
                    "truncated": False,
                    "error": f"Unsupported content type: {content_type}",
                }

            raw = response.read(MAX_BYTES + 1)
    except HTTPError as e:
        return {
            "success": False,
            "status_code": e.code,
            "final_url": e.geturl(),
            "content_type": e.headers.get("Content-Type", "") if e.headers else "",
            "text": "",
            "truncated": False,
            "error": f"HTTP error {e.code}: {e.reason}",
        }
    except URLError as e:
        return {
            "success": False,
            "status_code": 0,
            "final_url": url,
            "content_type": "",
            "text": "",
            "truncated": False,
            "error": f"Network error: {e.reason}",
        }
    except TimeoutError:
        return {
            "success": False,
            "status_code": 0,
            "final_url": url,
            "content_type": "",
            "text": "",
            "truncated": False,
            "error": "Network error: timed out",
        }
    except OSError as e:
        return {
            "success": False,
            "status_code": 0,
            "final_url": url,
            "content_type": "",
            "text": "",
            "truncated": False,
            "error": f"Network error: {e}",
        }

    byte_truncated = len(raw) > MAX_BYTES
    if byte_truncated:
        raw = raw[:MAX_BYTES]

    encoding = _charset_from_content_type(content_type)
    text = raw.decode(encoding, errors="replace")
    if "html" in content_type.lower():
        text = _html_to_text(text)

    char_truncated = len(text) > safe_max_chars
    if char_truncated:
        text = text[:safe_max_chars] + "\n... [content truncated]"

    return {
        "success": True,
        "status_code": status_code,
        "final_url": final_url,
        "content_type": content_type,
        "text": text,
        "truncated": byte_truncated or char_truncated,
        "error": "",
    }
