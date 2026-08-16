from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import locale
import re
import socket
from typing import Any
from urllib import error, parse, request

from .base import BaseTool, ToolOutput


class WebRequestTool(BaseTool):
    execution_mode = "parallel"

    def __init__(
        self,
        *,
        allow_private_network: bool = False,
        max_redirects: int = 5,
    ) -> None:
        self.allow_private_network = allow_private_network
        self.max_redirects = max_redirects

    name = "fetch_web_page"
    description = "Fetch a public web page with an HTTP GET request and return readable text content."
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full public http or https URL to fetch.",
            },
            "timeout": {
                "type": "number",
                "description": "Request timeout in seconds.",
                "default": 20,
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum number of extracted text characters to return.",
                "default": 4000,
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    async def run(self, arguments: dict[str, Any]) -> ToolOutput:
        url = str(arguments.get("url", "")).strip()
        if not url:
            raise ValueError("url is required")

        normalized_url = _normalize_web_url(url)
        _validate_web_url(normalized_url, allow_private_network=self.allow_private_network)

        timeout = _read_positive_float(arguments.get("timeout", 20), name="timeout")
        max_chars = _read_positive_int(arguments.get("max_chars", 4000), name="max_chars")

        return await asyncio.to_thread(
            self._fetch_web_page,
            url,
            normalized_url,
            timeout,
            max_chars,
        )

    def _fetch_web_page(
        self,
        requested_url: str,
        url: str,
        timeout: float,
        max_chars: int,
    ) -> dict[str, Any]:
        http_request = request.Request(
            url=url,
            headers={"User-Agent": "EchoBot/1.0"},
            method="GET",
        )
        max_bytes = max_chars * 4
        opener = request.build_opener(
            _ValidatedRedirectHandler(
                allow_private_network=self.allow_private_network,
                max_redirects=self.max_redirects,
            )
        )

        try:
            with opener.open(http_request, timeout=timeout) as response:
                final_url = response.geturl()
                _validate_web_url(final_url, allow_private_network=self.allow_private_network)
                raw_content = response.read(max_bytes + 1)
                content_type = response.headers.get("Content-Type", "")
                text, content_kind, encoding = _extract_web_text(
                    raw_content[:max_bytes],
                    content_type=content_type,
                    headers=response.headers,
                )
                content, text_truncated = _truncate_text(text, max_chars)

                return {
                    "requested_url": requested_url,
                    "url": final_url,
                    "status": response.status,
                    "content_type": content_type,
                    "content_kind": content_kind,
                    "encoding": encoding,
                    "content": content,
                    "total_chars": len(text),
                    "truncated": len(raw_content) > max_bytes or text_truncated,
                }
        except error.HTTPError as exc:
            detail = _read_http_error_detail(exc)
            if detail:
                raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except error.URLError as exc:
            if _is_timeout_error(exc.reason):
                raise RuntimeError(f"Network timeout after {timeout} seconds") from exc
            raise RuntimeError(f"Network error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError(f"Network timeout after {timeout} seconds") from exc


class _ValidatedRedirectHandler(request.HTTPRedirectHandler):
    def __init__(
        self,
        *,
        allow_private_network: bool,
        max_redirects: int,
    ) -> None:
        self.allow_private_network = allow_private_network
        self.max_redirections = max_redirects

    def redirect_request(
        self,
        req: request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> request.Request | None:
        _validate_web_url(newurl, allow_private_network=self.allow_private_network)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_web_url(url: str, *, allow_private_network: bool) -> None:
    parsed_url = parse.urlparse(url)
    if parsed_url.scheme not in {"http", "https"}:
        raise ValueError("url must start with http:// or https://")
    if not parsed_url.hostname:
        raise ValueError("url must include a host")

    if not allow_private_network:
        _validate_public_hostname(parsed_url.hostname)


def _normalize_web_url(url: str) -> str:
    parsed_url = parse.urlsplit(url)
    if not parsed_url.hostname:
        return url

    username = parsed_url.username
    password = parsed_url.password
    userinfo = ""
    if username is not None:
        userinfo = parse.quote(username, safe="")
        if password is not None:
            userinfo += f":{parse.quote(password, safe='')}"
        userinfo += "@"

    hostname = parsed_url.hostname.encode("idna").decode("ascii")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    netloc = f"{userinfo}{hostname}"
    if parsed_url.port is not None:
        netloc = f"{netloc}:{parsed_url.port}"

    path = parse.quote(parsed_url.path, safe="/:@!$&'()*+,;=-._~%")
    query = parse.quote(parsed_url.query, safe="/?:@!$&'()*+,;=-._~%[]")
    fragment = parse.quote(parsed_url.fragment, safe="/?:@!$&'()*+,;=-._~%[]")

    return parse.urlunsplit(
        (parsed_url.scheme, netloc, path, query, fragment),
    )


def _validate_public_hostname(hostname: str) -> None:
    normalized_host = hostname.strip().rstrip(".").lower()
    if not normalized_host:
        raise ValueError("url must include a host")
    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        raise ValueError("Private network addresses are not allowed")

    ip_address = _parse_ip_address(normalized_host)
    if ip_address is not None:
        _validate_public_ip(ip_address)
        return

    try:
        lookup_name = normalized_host.encode("idna").decode("ascii")
        resolved = socket.getaddrinfo(lookup_name, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve host: {hostname}") from exc

    for _, _, _, _, sockaddr in resolved:
        resolved_ip = _parse_ip_address(sockaddr[0])
        if resolved_ip is not None:
            _validate_public_ip(resolved_ip)


def _parse_ip_address(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    clean_host = hostname.split("%", 1)[0]
    try:
        return ipaddress.ip_address(clean_host)
    except ValueError:
        return None


def _validate_public_ip(ip_address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if (
        ip_address.is_private
        or ip_address.is_loopback
        or ip_address.is_link_local
        or ip_address.is_multicast
        or ip_address.is_reserved
        or ip_address.is_unspecified
    ):
        raise ValueError(f"Private network addresses are not allowed: {ip_address}")


def _extract_web_text(
    raw_content: bytes,
    *,
    content_type: str,
    headers: Any,
) -> tuple[str, str, str]:
    normalized_content_type = _normalize_content_type(content_type)
    looks_like_html = _looks_like_html(raw_content)
    encoding = _pick_web_encoding(
        raw_content,
        declared_encoding=headers.get_content_charset(),
        looks_like_html=looks_like_html,
    )
    decoded_text = raw_content.decode(encoding, errors="replace")

    if normalized_content_type == "application/json" or normalized_content_type.endswith("+json"):
        return _format_json_text(decoded_text), "json", encoding

    if normalized_content_type in {"text/html", "application/xhtml+xml"} or looks_like_html:
        return _extract_text_from_html(decoded_text), "html", encoding

    if normalized_content_type and not _is_text_content_type(normalized_content_type):
        raise ValueError(f"Only text responses are supported, got {normalized_content_type}")

    if not normalized_content_type and _looks_like_binary(raw_content):
        raise ValueError("Only text responses are supported")

    return decoded_text.strip(), "text", encoding


def _normalize_content_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _is_text_content_type(content_type: str) -> bool:
    return (
        content_type.startswith("text/")
        or content_type in {"application/xml", "text/xml", "application/javascript"}
        or content_type.endswith("+xml")
    )


def _pick_web_encoding(
    raw_content: bytes,
    *,
    declared_encoding: str | None,
    looks_like_html: bool,
) -> str:
    candidate_encodings: list[str] = []
    bom_encoding = _detect_bom_encoding(raw_content)
    if bom_encoding:
        candidate_encodings.append(bom_encoding)

    if declared_encoding:
        candidate_encodings.append(declared_encoding)

    if looks_like_html:
        html_encoding = _find_html_charset(raw_content)
        if html_encoding:
            candidate_encodings.append(html_encoding)

    preferred_encoding = locale.getpreferredencoding(False) or "utf-8"
    candidate_encodings.extend(["utf-8", preferred_encoding])

    seen_encodings: set[str] = set()
    for encoding in candidate_encodings:
        normalized = encoding.lower()
        if normalized in seen_encodings:
            continue
        seen_encodings.add(normalized)
        try:
            raw_content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
        return encoding

    if declared_encoding:
        return declared_encoding
    return "utf-8"


def _detect_bom_encoding(raw_content: bytes) -> str | None:
    if raw_content.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw_content.startswith(b"\xff\xfe"):
        return "utf-16"
    if raw_content.startswith(b"\xfe\xff"):
        return "utf-16"
    return None


def _find_html_charset(raw_content: bytes) -> str | None:
    preview = raw_content[:4096].decode("ascii", errors="ignore")
    match = re.search(
        r"<meta[^>]+charset=['\"]?\s*([A-Za-z0-9._-]+)",
        preview,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)

    match = re.search(
        r'content=["\'][^"\']*charset=([A-Za-z0-9._-]+)',
        preview,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)

    return None


def _format_json_text(text: str) -> str:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text.strip()

    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _extract_text_from_html(text: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</(p|div|section|article|header|footer|li|ul|ol|h[1-6]|tr)>", "\n", cleaned)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _looks_like_html(raw_content: bytes) -> bool:
    preview = raw_content[:512].decode("ascii", errors="ignore").lstrip().lower()
    return preview.startswith("<!doctype html") or preview.startswith("<html") or "<body" in preview


def _looks_like_binary(raw_content: bytes) -> bool:
    if not raw_content:
        return False

    preview = raw_content[:512]
    if b"\x00" in preview:
        return True

    control_bytes = 0
    for value in preview:
        if value < 32 and value not in {9, 10, 13}:
            control_bytes += 1

    return control_bytes > max(8, len(preview) // 10)


def _read_http_error_detail(exc: error.HTTPError) -> str:
    try:
        raw_content = exc.read(512)
    except OSError:
        return ""

    if not raw_content:
        return ""

    content_type = exc.headers.get("Content-Type", "")
    try:
        text, _, _ = _extract_web_text(raw_content, content_type=content_type, headers=exc.headers)
    except ValueError:
        return ""

    return text[:200]


def _read_positive_int(value: Any, *, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if number <= 0:
        raise ValueError(f"{name} must be greater than 0")

    return number


def _read_positive_float(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc

    if number <= 0:
        raise ValueError(f"{name} must be greater than 0")

    return number


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False

    return text[:max_chars], True


def _is_timeout_error(reason: object) -> bool:
    return isinstance(reason, TimeoutError) or isinstance(reason, socket.timeout)
