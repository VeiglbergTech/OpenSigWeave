"""Safe one-shot HTTPS import of a managed signature image (never at send time)."""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import quote, urlsplit

from signature_storage import MAX_SIGNATURE_IMAGE_BYTES, SignatureStorageError


class SignatureImageImportError(ValueError):
    """A safe error to show to an administrator without leaking the source URL."""


_ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_MAX_URL_LENGTH = 2048
_TOTAL_TIMEOUT = 10.0


def _validated_url(url: str) -> tuple[str, str]:
    if not isinstance(url, str) or not 12 <= len(url) <= _MAX_URL_LENGTH:
        raise SignatureImageImportError("Invalid image URL")
    if any(ord(char) < 32 or char == "\\"
           for char in url):
        raise SignatureImageImportError("Invalid image URL")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme.casefold() != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.fragment or parsed.port not in (None, 443)):
            raise SignatureImageImportError("Image URL must use HTTPS without credentials or redirects")
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if not hostname or len(hostname) > 253:
            raise SignatureImageImportError("Invalid image URL host")
        target = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        if parsed.query:
            target += "?" + quote(parsed.query, safe="/%:@!$&'()*+,;=?-._~")
        if len(target) > _MAX_URL_LENGTH:
            raise SignatureImageImportError("Image URL is too long")
        target.encode("ascii")
        return hostname, target
    except (ValueError, UnicodeError) as exc:
        raise SignatureImageImportError("Invalid image URL") from exc


def _resolve_public_address(host: str) -> str:
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise SignatureImageImportError("Image host cannot be resolved") from exc
    if not answers:
        raise SignatureImageImportError("Image host cannot be resolved")
    candidates = []
    for _family, _type, _proto, _canon, sockaddr in answers:
        try:
            ip = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
        except ValueError as exc:
            raise SignatureImageImportError("Invalid resolved image host") from exc
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise SignatureImageImportError("Image host resolves to a blocked network")
        candidates.append(str(ip))
    return candidates[0]


def import_image_from_url(file_store, url: str) -> str:
    """Pin validated public DNS results to the TLS socket. Never follow redirects.

    No proxy is used. HTTPS verifies the original hostname, not the pinned IP.
    Only validated image bytes are handed to the existing content-addressed store.
    """
    hostname, target = _validated_url(url)
    address = _resolve_public_address(hostname)
    deadline = time.monotonic() + _TOTAL_TIMEOUT
    try:
        with socket.create_connection((address, 443), timeout=5.0) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=hostname) as tls:
                request = (
                    f"GET {target} HTTP/1.1\r\n"
                    f"Host: {hostname}\r\nAccept: image/png,image/jpeg,image/webp,image/gif\r\n"
                    "Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
                )
                tls.sendall(request.encode("ascii"))
                response = http.client.HTTPResponse(tls)
                response.begin()
                if response.status != 200:
                    raise SignatureImageImportError("Image download failed (redirects are not followed)")
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise SignatureImageImportError("Compressed image responses are not allowed")
                media_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
                if media_type not in _ALLOWED_TYPES:
                    raise SignatureImageImportError("Unsupported remote image type")
                length = response.getheader("Content-Length")
                if length is not None and (not length.isdecimal() or int(length) > MAX_SIGNATURE_IMAGE_BYTES):
                    raise SignatureImageImportError("Remote image exceeds size limit")
                chunks, size = [], 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SignatureImageImportError("Image download timed out")
                    tls.settimeout(max(0.1, remaining))
                    block = response.read(min(65536, MAX_SIGNATURE_IMAGE_BYTES + 1 - size))
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_SIGNATURE_IMAGE_BYTES:
                        raise SignatureImageImportError("Remote image exceeds size limit")
                    chunks.append(block)
    except SignatureImageImportError:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError) as exc:
        raise SignatureImageImportError("Image download failed") from exc
    try:
        return file_store.write_image(b"".join(chunks), media_type)
    except (SignatureStorageError, TypeError) as exc:
        raise SignatureImageImportError("Downloaded image failed validation") from exc
