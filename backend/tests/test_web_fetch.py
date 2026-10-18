"""Offline transport tests: no test is permitted to contact a real hostname."""
from io import BytesIO
import socket
from time import monotonic, sleep
from urllib.parse import urlsplit

from fastapi import HTTPException
import pytest

from backend.services import web_fetch as web


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/file", "javascript:alert(1)",
    "http://localhost/", "http://foo.local/", "http://foo.internal/",
    "http://127.0.0.1/", "http://10.0.0.1/", "http://169.254.169.254/",
    "http://192.168.1.1/", "http://100.64.0.1/", "http://0.0.0.0/",
    "http://[::1]/", "http://[fe80::1]/", "http://[fec0::1]/",
    "http://[::ffff:127.0.0.1]/", "http://[ff02::1]/", "http://2130706433/",
    "https://example.com:80/", "http://example.com:443/", "https://example.com:8080/", "https://example.com:0/read",
    "https://user:pass@example.com/", "https://user@example.com/",
    "https://example.com\\@127.0.0.1/", "https://example.com/a\nb",
    "https://example.com/%0d%0aHost:other", "https://[fe80::1%25eth0]/",
    "https://ex ample.com/", "https://example.com/a b", "https://example.com/" + "x" * 2048,
])
def test_rejects_unsafe_url_without_dns(url, monkeypatch):
    monkeypatch.setattr(web, "_lookup", lambda *_: pytest.fail("unsafe URL reached DNS"))
    with pytest.raises(HTTPException) as error:
        web.fetch_page(url)
    assert error.value.status_code == 422


def test_canonical_url_keeps_path_query_not_credentials_or_fragment():
    assert web.normalize_url("  HTTPS://ExAmPlE.COM.:443/a?x=1&x=2#part  ") == "https://example.com/a?x=1&x=2"
    assert web.normalize_url("https://例子.公司/英语?q=学习") == "https://xn--fsqu00a.xn--55qx5d/%E8%8B%B1%E8%AF%AD?q=%E5%AD%A6%E4%B9%A0"
    assert web.normalize_url("https://[2606:4700:4700::1111]/") == "https://[2606:4700:4700::1111]/"


@pytest.mark.parametrize("addresses", [
    [], ["127.0.0.1"], ["10.0.0.2"], ["::1"], ["::ffff:192.168.0.1"],
    ["93.184.216.34", "169.254.169.254"], ["93.184.216.34", "fc00::1"],
    ["not-an-address"], ["224.0.0.1"], ["255.255.255.255"],
])
def test_all_dns_answers_must_be_public(addresses, monkeypatch):
    monkeypatch.setattr(web, "_lookup", lambda *_: addresses)
    monkeypatch.setattr(web, "_open_response", lambda *_: pytest.fail("unsafe DNS reached transport"))
    with pytest.raises(HTTPException) as error:
        web.fetch_page("https://example.com/")
    assert error.value.status_code == 422


class Connection:
    def __init__(self):
        self.sock = self
        self.timeouts = []
        self.closed = False

    def settimeout(self, value):
        self.timeouts.append(value)

    def close(self):
        self.closed = True


class Response(BytesIO):
    def __init__(self, data=b"<html>Article</html>", status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


def transport(monkeypatch, responses):
    calls, connections, lookups = [], [], []

    def lookup(host, port):
        lookups.append((host, port))
        return ["93.184.216.34"]

    def open_response(url, address, deadline):
        calls.append((url, address))
        connection = Connection()
        connections.append(connection)
        value = responses[len(calls) - 1]
        if isinstance(value, Exception):
            raise value
        return connection, value

    monkeypatch.setattr(web, "_lookup", lookup)
    monkeypatch.setattr(web, "_open_response", open_response)
    return calls, connections, lookups


def test_redirect_each_hop_revalidates_and_no_cookie_or_html_canonical_request(monkeypatch):
    first = Response(status=302, headers={"Location": "https://news.example.com/story#intro", "Set-Cookie": "private=secret"})
    second = Response(b'<html><link rel="canonical" href="http://127.0.0.1">Final</html>')
    calls, connections, lookups = transport(monkeypatch, [first, second])
    html, final = web.fetch_page("https://example.com/start")
    assert final == "https://news.example.com/story" and "Final" in html
    assert calls == [("https://example.com/start", "93.184.216.34"), (final, "93.184.216.34")]
    assert lookups == [("example.com", 443), ("news.example.com", 443)]
    assert all(connection.closed for connection in connections)
    assert first.closed and second.closed


@pytest.mark.parametrize("location", ["http://127.0.0.1/admin", "http://[::1]/", "https://user:pass@example.com/", "file:///secret"])
def test_redirect_to_unsafe_target_never_connects(location, monkeypatch):
    calls, connections, _ = transport(monkeypatch, [Response(status=302, headers={"Location": location})])
    with pytest.raises(HTTPException) as error:
        web.fetch_page("https://example.com/")
    assert error.value.status_code == 422 and len(calls) == 1 and connections[0].closed


@pytest.mark.parametrize("response,status", [
    (Response(status=403), 422), (Response(status=401), 422),
    (Response(headers={"Content-Type": "application/pdf"}), 415),
    (Response(headers={"Content-Encoding": "gzip"}), 415),
    (Response(headers={"Content-Length": str(web.MAX_HTML_BYTES + 1)}), 413),
    (Response(b"x" * (web.MAX_HTML_BYTES + 1)), 413),
    (Response(headers={"Content-Length": "not a number"}), 413),
    (Response(headers={"Content-Length": "500"}), 422),
    (Response(headers={"Content-Type": "text/html; charset=unicode_escape"}), 422),
    (Response(status=302), 422),
])
def test_bounded_response_and_clear_failures(response, status, monkeypatch):
    calls, connections, _ = transport(monkeypatch, [response])
    with pytest.raises(HTTPException) as error:
        web.fetch_page("https://example.com/")
    assert error.value.status_code == status and connections[0].closed and response.closed


def test_redirect_count_is_bounded(monkeypatch):
    calls, connections, _ = transport(monkeypatch, [Response(status=302, headers={"Location": "/again"}) for _ in range(4)])
    with pytest.raises(HTTPException) as error:
        web.fetch_page("https://example.com/")
    assert error.value.status_code == 422 and len(calls) == 4 and all(conn.closed for conn in connections)


def test_network_errors_are_sanitized(monkeypatch):
    transport(monkeypatch, [OSError("private proxy/password details")])
    with pytest.raises(HTTPException) as error:
        web.fetch_page("https://example.com/")
    assert error.value.status_code == 422 and "password" not in error.value.detail


def test_unreachable_ipv6_retries_only_validated_ipv4_without_new_dns(monkeypatch):
    calls, connections, _ = transport(monkeypatch, [OSError("IPv6 unreachable"), Response()])
    lookups = []

    def lookup(*args):
        lookups.append(args)
        return ["2606:4700:4700::1111", "93.184.216.34"]

    monkeypatch.setattr(web, "_lookup", lookup)
    assert web.fetch_page("https://example.com/")[1] == "https://example.com/"
    assert [address for _, address in calls] == ["2606:4700:4700::1111", "93.184.216.34"]
    assert len(lookups) == 1 and connections[-1].closed


def test_timeout_on_dns_and_transport(monkeypatch):
    monkeypatch.setattr(web, "FETCH_SECONDS", .01)

    def slow(*_):
        sleep(.03)
        return ["93.184.216.34"]

    monkeypatch.setattr(web, "_lookup", slow)
    with pytest.raises(HTTPException) as error:
        web.fetch_page("https://example.com/")
    assert error.value.status_code == 504
    transport(monkeypatch, [socket.timeout("sensitive")])
    with pytest.raises(HTTPException) as error:
        web.fetch_page("https://example.com/")
    assert error.value.status_code == 504 and "sensitive" not in error.value.detail


def test_total_deadline_bounds_dripped_headers(monkeypatch):
    now = [0.0]

    class Drip:
        def settimeout(self, value):
            assert value <= 1

        def makefile(self, mode, buffering=0):
            return self

        def readinto(self, buffer):
            now[0] += .6
            buffer[0] = 65
            return 1

        def close(self):
            pass

    monkeypatch.setattr(web, "monotonic", lambda: now[0])
    reader = web._DeadlineReader(Drip(), 1)
    with pytest.raises(HTTPException) as error:
        from io import BufferedReader
        BufferedReader(reader).readline()
    assert error.value.status_code == 504 and now[0] < 2


def test_real_httpresponse_close_connection_keeps_body_file_alive():
    left, right = socket.socketpair()
    connection = web.http.client.HTTPConnection("example.com")
    connection.sock = web._DeadlineSocket(left, monotonic() + 3)
    try:
        connection.request("GET", "/", headers={"Connection": "close"})
        assert right.recv(4096).startswith(b"GET / HTTP/1.1")
        right.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 4\r\n\r\nBody")
        response = connection.getresponse()
        assert connection.sock is None  # stdlib has closed its connection owner.
        assert response.read1(65536) == b"Body"
        assert response.read1(65536) == b""
        response.close()
    finally:
        connection.close()
        right.close()
        left.close()


def test_connect_pins_numeric_ip_and_keeps_tls_sni_host_and_no_proxy(monkeypatch):
    sockets, tls, requests = [], [], []

    class FakeSocket:
        def __init__(self, family, kind):
            self.family, self.kind = family, kind
            self.target = None
            sockets.append(self)

        def settimeout(self, _):
            pass

        def connect(self, target):
            self.target = target

        def close(self):
            pass

        def sendall(self, data):
            requests.append(data)

    class Context:
        def wrap_socket(self, sock, server_hostname):
            tls.append(server_hostname)
            return sock

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setattr(web.socket, "socket", FakeSocket)
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *_args, **_kw: pytest.fail("connection performed a second DNS lookup"))
    monkeypatch.setattr(web.ssl, "create_default_context", Context)
    monkeypatch.setattr(web.http.client.HTTPConnection, "getresponse", lambda self: "response")
    connection, response = web._open_response("https://news.example.com/story?q=1", "93.184.216.34", monotonic() + 5)
    assert sockets[0].target == ("93.184.216.34", 443)
    assert tls == ["news.example.com"] and response == "response"
    wire = b"".join(requests)
    assert b"GET /story?q=1 HTTP/1.1" in wire and b"Host: news.example.com" in wire
    assert b"Authorization" not in wire and b"Cookie:" not in wire and b"identity" in wire
    connection.close()
