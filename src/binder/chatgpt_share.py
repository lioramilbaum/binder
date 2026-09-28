"""Import the text snapshot embedded in a public ChatGPT share page.

The page format is undocumented. Parse only the known streamed payload and fail
closed when its conversation shape is no longer recognizable.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
import threading
import time as clock
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from uuid import UUID

MAX_DOWNLOAD = 4_000_000
MAX_DECODED = 8_000_000
MAX_TRANSCRIPT = 200_000
MAX_REDIRECTS = 3
MAX_ADDRESSES = 8
TIMEOUT = 5
TOTAL_TIMEOUT = 12
_DNS_SLOTS = threading.BoundedSemaphore(4)
_ENQUEUE = re.compile(r"streamController\.enqueue\(\s*")
_INTERNAL_CONTENT_TYPES = frozenset({
    "thoughts", "reasoning_recap", "model_editable_context",
    "tool_response", "execution_output", "model_set_context",
})


class ShareImportError(Exception):
    """A safe, user-facing error category; never contains remote response data."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SharePreview:
    source_url: str
    title: str
    transcript: str
    message_count: int
    warnings: tuple[str, ...]
    source_first_message_at: str | None = None


def _source_message_time(value: object) -> str | None:
    """Canonical UTC time from a numeric Unix timestamp, if representable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        seconds = float(value)
        if not math.isfinite(seconds):
            return None
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="microseconds")
    except (OverflowError, OSError, ValueError):
        return None


class _Deadline:
    def __init__(self):
        self.expires = clock.monotonic() + TOTAL_TIMEOUT

    def remaining(self) -> float:
        remaining = self.expires - clock.monotonic()
        if remaining <= 0:
            raise ShareImportError("inaccessible_link")
        return remaining


def validate_share_url(value: str) -> str:
    if (
        not isinstance(value, str) or len(value) > 200 or value != value.strip()
        or any(ord(char) < 33 or ord(char) == 127 for char in value)
    ):
        raise ShareImportError("invalid_link")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https" or parsed.netloc != "chatgpt.com"
            or parsed.username is not None or parsed.password is not None
            or parsed.port is not None or parsed.query or parsed.fragment
        ):
            raise ValueError
        match = re.fullmatch(r"/share/([0-9a-fA-F-]{36})/?", parsed.path)
        if match is None or str(UUID(match.group(1))) != match.group(1).lower():
            raise ValueError
    except (ValueError, AttributeError):
        raise ShareImportError("invalid_link") from None
    return f"https://chatgpt.com/share/{match.group(1).lower()}"


def _public_addresses(host: str, deadline: _Deadline | None = None) -> list[str]:
    try:
        if deadline is None:
            answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        else:
            # getaddrinfo has no timeout. Bound pending lookups with a small
            # daemon pool; timed-out workers never proceed to a connection.
            if not _DNS_SLOTS.acquire(timeout=deadline.remaining()):
                raise ShareImportError("inaccessible_link")
            done = threading.Event()
            result: list[object] = []

            def resolve() -> None:
                try:
                    result.append(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
                except Exception:
                    result.append(None)
                finally:
                    _DNS_SLOTS.release()
                    done.set()

            try:
                threading.Thread(target=resolve, daemon=True).start()
            except Exception:
                _DNS_SLOTS.release()
                raise ShareImportError("inaccessible_link") from None
            if not done.wait(deadline.remaining()):
                raise ShareImportError("inaccessible_link")
            deadline.remaining()
            if not result or result[0] is None:
                raise ShareImportError("inaccessible_link")
            answers = result[0]
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ShareImportError("inaccessible_link")
        return addresses
    except (OSError, ValueError, IndexError):
        raise ShareImportError("inaccessible_link") from None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, deadline: _Deadline | None = None):
        super().__init__(host, timeout=TIMEOUT, context=ssl.create_default_context())
        self._validated_address = address
        self._deadline = deadline
        self._active_socket: socket.socket | None = None

    def set_phase_timeout(self) -> None:
        if self._deadline is None:
            return
        self.timeout = min(TIMEOUT, self._deadline.remaining())
        if self.sock is not None:
            self.sock.settimeout(self.timeout)

    def abort(self) -> None:
        sock = self._active_socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def connect(self) -> None:
        # No urllib opener or environment proxy. TLS still authenticates the
        # requested hostname, while the TCP socket uses the validated IP.
        self.set_phase_timeout()
        raw = socket.create_connection((self._validated_address, 443), self.timeout)
        self._active_socket = raw
        try:
            self.set_phase_timeout()
            raw.settimeout(self.timeout)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
            self._active_socket = self.sock
            self.set_phase_timeout()
        except BaseException:
            raw.close()
            raise


def fetch_share_page(url: str) -> tuple[str, bytes]:
    current = validate_share_url(url)
    deadline = _Deadline()
    for redirect in range(MAX_REDIRECTS + 1):
        # Validate every DNS answer, then try a bounded number of pinned
        # addresses. IPv4 first avoids a long IPv6 connect on hosts without
        # working IPv6; IPv6 remains available as a fallback.
        addresses = sorted(
            _public_addresses("chatgpt.com", deadline),
            key=lambda address: ipaddress.ip_address(address).version,
        )[:MAX_ADDRESSES]
        redirected = False
        for address in addresses:
            deadline.remaining()
            connection = None
            timer = None
            try:
                connection = _PinnedHTTPSConnection("chatgpt.com", address, deadline)
                timer = threading.Timer(deadline.remaining(), connection.abort)
                timer.daemon = True
                timer.start()
                connection.set_phase_timeout()
                connection.request(
                    "GET", urlsplit(current).path,
                    headers={
                        "Accept": "text/html",
                        "Accept-Encoding": "gzip, deflate",
                        "User-Agent": "Binder/1.0",
                    },
                )
                connection.set_phase_timeout()
                response = connection.getresponse()
                deadline.remaining()
                if response.status in (301, 302, 303, 307, 308):
                    if redirect >= MAX_REDIRECTS:
                        raise ShareImportError("inaccessible_link")
                    target = response.getheader("Location")
                    if not target:
                        raise ShareImportError("inaccessible_link")
                    current = validate_share_url(urljoin(current, target))
                    deadline.remaining()
                    redirected = True
                    break
                if response.status != 200:
                    raise ShareImportError("inaccessible_link")
                content_type = response.getheader("Content-Type", "")
                encoding = response.getheader("Content-Encoding", "identity")
                if not isinstance(content_type, str) or content_type.split(";", 1)[0].strip().lower() != "text/html":
                    raise ShareImportError("unsupported_format")
                if not isinstance(encoding, str) or encoding.lower() not in ("identity", "gzip", "deflate"):
                    raise ShareImportError("unsupported_format")
                chunks = []
                size = 0
                while True:
                    connection.set_phase_timeout()
                    chunk = response.read(64_000)
                    deadline.remaining()
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise ShareImportError("too_large")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                if encoding.lower() != "identity":
                    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding.lower() == "gzip" else zlib.MAX_WBITS)
                    try:
                        decoded = decompressor.decompress(raw, MAX_DECODED + 1)
                        if len(decoded) > MAX_DECODED or decompressor.unconsumed_tail:
                            raise ShareImportError("too_large")
                        decoded += decompressor.flush(MAX_DECODED + 1 - len(decoded))
                        if len(decoded) > MAX_DECODED or not decompressor.eof:
                            raise ShareImportError("too_large")
                        if decompressor.unused_data:
                            raise ShareImportError("unsupported_format")
                        raw = decoded
                    except zlib.error:
                        raise ShareImportError("unsupported_format") from None
                if len(raw) > MAX_DECODED:
                    raise ShareImportError("too_large")
                deadline.remaining()
                return current, raw
            except ShareImportError:
                raise
            except (OSError, http.client.HTTPException):
                # The next address has already passed the same public-IP
                # validation, and the overall deadline still applies.
                continue
            finally:
                if timer is not None:
                    timer.cancel()
                if connection is not None:
                    connection.close()
        if not redirected:
            raise ShareImportError("inaccessible_link")
    raise ShareImportError("inaccessible_link")


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts: list[str] = []
        self._parts: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._parts = []

    def handle_data(self, data):
        if self._parts is not None:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._parts is not None:
            self.scripts.append("".join(self._parts))
            self._parts = None


def _flight_pool(page: str) -> list:
    parser = _Scripts()
    parser.feed(page)
    pools = []
    decoder = json.JSONDecoder()
    for script in parser.scripts:
        for match in _ENQUEUE.finditer(script):
            try:
                chunk, _ = decoder.raw_decode(script, match.end())
                if isinstance(chunk, str) and chunk.lstrip().startswith("["):
                    pool = json.loads(chunk)
                    if isinstance(pool, list):
                        pools.append(pool)
            except (ValueError, json.JSONDecodeError):
                raise ShareImportError("unsupported_format") from None
    if len(pools) != 1:
        raise ShareImportError("unsupported_format")
    return pools[0]


def _decode_pool(pool: list) -> dict:
    if len(pool) < 3 or len(pool) > 100_000:
        raise ShareImportError("unsupported_format")
    cache: dict[int, object] = {}
    active: set[int] = set()

    def resolve(value, depth=0):
        if depth > 60:
            raise ShareImportError("unsupported_format")
        if isinstance(value, int) and not isinstance(value, bool):
            if value < 0 or value >= len(pool):
                return value
            if value in active:
                return None
            if value not in cache:
                active.add(value)
                cache[value] = resolve(pool[value], depth + 1)
                active.remove(value)
            return cache[value]
        if isinstance(value, list):
            return [resolve(item, depth + 1) for item in value]
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if re.fullmatch(r"_\d+", key):
                    index = int(key[1:])
                    if index >= len(pool) or not isinstance(pool[index], str):
                        raise ShareImportError("unsupported_format")
                    key = pool[index]
                if key in result:
                    raise ShareImportError("unsupported_format")
                result[key] = resolve(item, depth + 1)
            return result
        return value

    # New format: pool is a flat array of key-value pairs starting at index 1
    # But the first element is often a metadata object, so we skip it
    root = {}
    for index in range(1, len(pool) - 1, 2):
        key = pool[index]
        if isinstance(key, str):
            root[key] = resolve(pool[index + 1])
    return root


def _conversation(root: object) -> dict:
    found: list[dict] = []
    seen: set[int] = set()

    def walk(node, depth=0):
        if depth > 60 or id(node) in seen:
            return
        if isinstance(node, (dict, list)):
            seen.add(id(node))
        if isinstance(node, dict):
            if "linear_conversation" in node:
                found.append(node)
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    walk(root)
    # The flattened Flight pool can also expose a synthetic root with a
    # linear_conversation key but no mapping. Prefer complete conversation
    # objects, then require all candidates to agree.
    complete = [candidate for candidate in found if isinstance(candidate.get("mapping"), dict)]
    if complete:
        found = complete
    if not found or any(candidate != found[0] for candidate in found[1:]) or not isinstance(found[0]["linear_conversation"], list):
        raise ShareImportError("unsupported_format")
    return found[0]


def parse_share_page(source_url: str, page: bytes) -> SharePreview:
    try:
        html = page.decode("utf-8", "strict")
        data = _conversation(_decode_pool(_flight_pool(html)))
        linear = data.get("linear_conversation", [])
        mapping = data.get("mapping", {})
        if not linear or not isinstance(mapping, dict):
            raise ShareImportError("unsupported_format")
        if len(linear) > 5000:
            raise ShareImportError("too_large")
        turns = []
        omitted = False
        non_visible_omitted = False
        source_first_message_at = None
        timestamp_unavailable = False
        for entry in linear:
            if not isinstance(entry, dict):
                raise ShareImportError("unsupported_format")
            node = entry
            if "message" not in node:
                node = mapping.get(entry.get("id"))
                if not isinstance(node, dict):
                    raise ShareImportError("unsupported_format")
            message = node.get("message")
            if message is None:
                continue
            if not isinstance(message, dict) or not isinstance(message.get("author"), dict):
                raise ShareImportError("unsupported_format")
            role = message["author"].get("role")
            if role not in ("user", "assistant"):
                non_visible_omitted = True
                continue
            metadata = message.get("metadata")
            if metadata is not None and not isinstance(metadata, dict):
                raise ShareImportError("unsupported_format")
            metadata = metadata or {}
            hidden = metadata.get("is_visually_hidden_from_conversation", False)
            recipient = message.get("recipient", "all")
            if not isinstance(hidden, bool) or (recipient is not None and not isinstance(recipient, str)):
                raise ShareImportError("unsupported_format")
            if hidden or recipient not in (None, "", "all"):
                non_visible_omitted = True
                continue
            content = message.get("content")
            if not isinstance(content, dict):
                raise ShareImportError("unsupported_format")
            kind = content.get("content_type")
            if kind in _INTERNAL_CONTENT_TYPES:
                non_visible_omitted = True
                continue
            if metadata.get("attachments"):
                omitted = True
            parts: list[str] = []
            if kind in ("text", "multimodal_text"):
                if not isinstance(content.get("parts"), list):
                    raise ShareImportError("unsupported_format")
                for part in content["parts"]:
                    if isinstance(part, str):
                        parts.append(part)
                    elif isinstance(part, dict):
                        part_kind = part.get("content_type", part.get("type"))
                        if part_kind in _INTERNAL_CONTENT_TYPES:
                            non_visible_omitted = True
                        elif part_kind in (None, "text"):
                            value = part.get("text")
                            if isinstance(value, str):
                                parts.append(value)
                            elif isinstance(value, list):
                                for segment in value:
                                    if isinstance(segment, str):
                                        parts.append(segment)
                                    else:
                                        omitted = True
                            else:
                                omitted = True
                        else:
                            omitted = True
                    else:
                        omitted = True
            elif kind == "code" and isinstance(content.get("text"), str):
                language = content.get("language", "")
                language = language if isinstance(language, str) and re.fullmatch(r"[\w+-]{0,30}", language) else ""
                parts.append(f"```{language}\n{content['text']}\n```")
            else:
                omitted = True
            text = "\n\n".join(parts).strip()
            if text:
                if not turns:
                    source_first_message_at = _source_message_time(message.get("create_time"))
                    timestamp_unavailable = source_first_message_at is None
                turns.append(f"{'Liora' if role == 'user' else 'AI'}:\n{text}")
        if not turns:
            raise ShareImportError("unsupported_format")
        transcript = "\n\n".join(turns)
        if len(transcript) > MAX_TRANSCRIPT:
            raise ShareImportError("too_large")
        title = data.get("pageTitle") or data.get("ogTitle") or data.get("title")
        title = title.strip() if isinstance(title, str) and title.strip() else "ChatGPT conversation"
        if len(title) > 200:
            title = title[:200]
        warnings = tuple(
            code for condition, code in (
                (non_visible_omitted, "non_visible_omitted"),
                (omitted, "non_text_omitted"),
                (timestamp_unavailable, "timestamp_unavailable"),
            ) if condition
        )
        return SharePreview(source_url, title, transcript, len(turns), warnings, source_first_message_at)
    except (UnicodeDecodeError, RecursionError, KeyError, TypeError, ValueError):
        raise ShareImportError("unsupported_format") from None


def import_share_url(url: str) -> SharePreview:
    source_url, page = fetch_share_page(url)
    return parse_share_page(source_url, page)
