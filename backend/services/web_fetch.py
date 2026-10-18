"""Read bounded public HTML, pinning every connection to a validated DNS answer.

No proxy, cookie jar, browser execution, credential forwarding or external asset
requests are involved. The original hostname still supplies HTTP Host and TLS SNI.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import codecs
import http.client
from io import BufferedReader, RawIOBase
import ipaddress
import re
import socket
import ssl
from threading import BoundedSemaphore
from time import monotonic
import unicodedata
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from fastapi import HTTPException


MAX_HTML_BYTES = 2 * 1024 * 1024
FETCH_SECONDS = 20
MAX_REDIRECTS = 3
_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="web-dns")
_DNS_SLOTS = BoundedSemaphore(4)


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return (address.is_global and not address.is_multicast and not address.is_reserved
                and not getattr(address, "is_site_local", False))
    except ValueError:
        return False


def normalize_url(value: str) -> str:
    """Normalize one URL without contacting its host. Never trust canonical HTML."""
    value = value.strip()
    if not value or len(value) > 2048 or "\\" in value:
        raise HTTPException(422, "请输入有效的公开网页链接，长度不能超过 2,048 个字符。")
    if any(char.isspace() or unicodedata.category(char) in {"Cc", "Cs"} for char in value):
        raise HTTPException(422, "网页链接不能包含空格或控制字符。")
    try:
        parts = urlsplit(value)
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError()
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            raise ValueError()
        port = parts.port if parts.port is not None else (443 if scheme == "https" else 80)
        if port != (443 if scheme == "https" else 80):
            raise ValueError()
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        # Percent escapes in hostnames include IPv6 zone identifiers and encoded
        # credentials; neither belongs in a public article URL.
        if not host or "%" in host or any(char in host for char in " /\\@"):
            raise ValueError()
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            if (len(host) > 253 or "." not in host or
                    any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                        for label in host.split(".")) or
                    host.endswith((".localhost", ".local", ".internal", ".test", ".invalid", ".onion"))):
                raise ValueError()
        else:
            if not _public_address(str(literal)):
                raise HTTPException(422, "只能导入公开互联网上的网页，不能访问本机或私有网络地址。")
            host = f"[{literal.compressed}]" if literal.version == 6 else literal.compressed
        # ASCII-only HTTP request target; preserve existing escapes and query order.
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
        if any(unicodedata.category(char) in {"Cc", "Cs"} for char in unquote(path + query)):
            raise ValueError()
        normalized = urlunsplit((scheme, host, path, query, ""))
        if len(normalized) > 2048:
            raise ValueError()
        return normalized
    except HTTPException:
        raise
    except (ValueError, UnicodeError):
        raise HTTPException(422, "只支持无账号密码的公开 HTTP/HTTPS 网页，且只能使用默认端口。") from None


def _remaining(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise HTTPException(504, "网页读取超过 20 秒，请稍后重试或粘贴文章正文。")
    return remaining


def _lookup(host: str, port: int) -> list[str]:
    return list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)))


def _resolve_public(host: str, port: int, deadline: float) -> list[str]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [str(literal)]
    else:
        if not _DNS_SLOTS.acquire(blocking=False):
            raise HTTPException(503, "网页地址解析繁忙，请稍后重试。")
        try:
            future = _DNS_POOL.submit(_lookup, host, port)
        except Exception:
            _DNS_SLOTS.release()
            raise
        future.add_done_callback(lambda _: _DNS_SLOTS.release())
        try:
            addresses = future.result(timeout=_remaining(deadline))
        except FutureTimeout:
            future.cancel()
            raise HTTPException(504, "网页地址解析超时，请稍后重试。") from None
        except OSError:
            raise HTTPException(422, "无法解析这个网页地址，请检查链接后重试。") from None
    # Reject a mixed public/private answer, rather than selecting its public part.
    if not addresses or any(not _public_address(address) for address in addresses):
        raise HTTPException(422, "只能导入公开互联网上的网页，不能访问本机或私有网络地址。")
    return addresses


class _DeadlineReader(RawIOBase):
    """Enforce the total deadline even while parsing dripped response headers."""
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline
        # Real socket.makefile retains an I/O reference. HTTPConnection closes
        # its socket ownership immediately for "Connection: close" responses;
        # the response must keep the descriptor alive until its stream closes.
        self.stream = sock.makefile("rb", buffering=0)

    def readable(self):
        return True

    def readinto(self, buffer):
        self.sock.settimeout(_remaining(self.deadline))
        return self.stream.readinto(buffer)

    def close(self):
        try:
            self.stream.close()
        finally:
            super().close()


class _DeadlineSocket:
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline

    def makefile(self, mode):
        if mode != "rb":
            raise ValueError("Only a binary response stream is supported")
        return BufferedReader(_DeadlineReader(self.sock, self.deadline))

    def sendall(self, data):
        self.sock.settimeout(_remaining(self.deadline))
        self.sock.sendall(data)

    def settimeout(self, value):
        self.sock.settimeout(min(value, _remaining(self.deadline)))

    def close(self):
        self.sock.close()


def _open_response(url: str, address: str, deadline: float):
    parts = urlsplit(url)
    host, port = parts.hostname, 443 if parts.scheme == "https" else 80
    connection = http.client.HTTPConnection(host, port, timeout=_remaining(deadline))
    # Do not call create_connection(host) or HTTPConnection.connect(): either
    # would resolve DNS a second time, allowing a rebinding attack.
    sock = socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM)
    connection.sock = sock
    try:
        sock.settimeout(min(5, _remaining(deadline)))
        sock.connect((address, port))
        if parts.scheme == "https":
            sock.settimeout(_remaining(deadline))
            connection.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        connection.sock = _DeadlineSocket(connection.sock, deadline)
        connection.sock.settimeout(_remaining(deadline))
        target = parts.path + ("?" + parts.query if parts.query else "")
        connection.request("GET", target, headers={
            "Host": parts.netloc, "User-Agent": "LexiRead/1.0 (article reader)",
            "Accept": "text/html, application/xhtml+xml;q=0.9",
            "Accept-Encoding": "identity", "Connection": "close",
        })
        connection.sock.settimeout(_remaining(deadline))
        return connection, connection.getresponse()
    except Exception:
        connection.close()
        raise


def _decode(data: bytes, content_type: str) -> str:
    match = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", content_type, re.I)
    if not match:
        match = re.search(rb"charset\s*=\s*[\"']?([\w.-]+)", data[:4096], re.I)
    encoding = match.group(1) if match else "utf-8"
    if isinstance(encoding, bytes):
        encoding = encoding.decode("ascii")
    try:
        # An arbitrary codec (e.g. unicode_escape) must not reinterpret markup.
        name = codecs.lookup(encoding).name
        if name not in {"utf-8", "utf-8-sig", "iso8859-1", "cp1252", "gbk", "gb18030", "big5", "ascii"}:
            raise LookupError()
        return data.decode(encoding, errors="replace")
    except LookupError:
        raise HTTPException(422, "网页使用暂不支持的文字编码，请复制正文后粘贴导入。") from None


def fetch_page(url: str) -> tuple[str, str]:
    deadline = monotonic() + FETCH_SECONDS
    current = normalize_url(url)
    try:
        for hop in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            addresses = _resolve_public(parts.hostname, 443 if parts.scheme == "https" else 80, deadline)
            # IPv6 may be published even when this computer only has IPv4.
            # Retry only the already validated DNS answers, within one deadline.
            candidates = addresses[:8]
            for index, address in enumerate(candidates):
                try:
                    connection, response = _open_response(current, address, deadline)
                    break
                except (OSError, http.client.HTTPException):
                    if index == len(candidates) - 1:
                        raise
            try:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location or hop == MAX_REDIRECTS:
                        raise HTTPException(422, "网页重定向过多或目标无效，请使用文章的最终链接。")
                    current = normalize_url(urljoin(current, location))
                    continue
                if response.status != 200:
                    raise HTTPException(422, "网站拒绝读取或文章不可访问；登录、付费和反爬页面请手动粘贴正文。")
                content_type = response.getheader("Content-Type", "")
                if content_type.split(";", 1)[0].strip().lower() not in {"text/html", "application/xhtml+xml"}:
                    raise HTTPException(415, "链接不是 HTML 网页；PDF 请下载后从 PDF 入口导入。")
                if response.getheader("Content-Encoding", "identity").strip().lower() != "identity":
                    raise HTTPException(415, "网页未提供可直接读取的 HTML，请复制正文后粘贴导入。")
                length = response.getheader("Content-Length")
                if length and (not length.isdecimal() or int(length) > MAX_HTML_BYTES):
                    raise HTTPException(413, "网页内容超过 2 MB，请选择正文较短的网页或粘贴文章。")
                chunks, size = [], 0
                while True:
                    # HTTPConnection may relinquish sock on a Connection: close
                    # response. Its response owns the same live socket in that case.
                    active_socket = connection.sock or getattr(response.fp, "raw", None)
                    if active_socket is not None:
                        if not hasattr(active_socket, "settimeout"):
                            active_socket = getattr(active_socket, "_sock", None)
                        if active_socket is not None:
                            active_socket.settimeout(_remaining(deadline))
                    chunk = response.read1(65536)
                    _remaining(deadline)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_HTML_BYTES:
                        raise HTTPException(413, "网页内容超过 2 MB，请选择正文较短的网页或粘贴文章。")
                    chunks.append(chunk)
                if length and size != int(length):
                    raise HTTPException(422, "网页传输不完整，请重试或粘贴正文。")
                return _decode(b"".join(chunks), content_type), current
            finally:
                response.close()
                connection.close()
    except HTTPException:
        raise
    except (TimeoutError, socket.timeout):
        raise HTTPException(504, "网页读取超过 20 秒，请稍后重试或粘贴文章正文。") from None
    except (OSError, http.client.HTTPException, ValueError, OverflowError):
        raise HTTPException(422, "无法安全读取这个网页，请检查链接或复制正文后粘贴导入。") from None
    raise HTTPException(422, "无法读取这个网页。")
