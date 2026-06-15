import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Validate URL safety by checking scheme and checking resolved IPs against private ranges (SSRF protection)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"Unsupported scheme: {parsed.scheme}"

        host = parsed.hostname
        if not host:
            return False, "Empty or invalid hostname"

        # Try parsing directly as an IP address
        try:
            ip = ipaddress.ip_address(host)
            ip_objs = [ip]
        except ValueError:
            # Resolve DNS
            try:
                addr_info = socket.getaddrinfo(host, None)
                ip_objs = []
                for item in addr_info:
                    ip_str = item[4][0]
                    if isinstance(ip_str, str):
                        if "%" in ip_str:
                            ip_str = ip_str.split("%")[0]
                        ip_objs.append(ipaddress.ip_address(ip_str))
            except Exception as e:
                return False, f"DNS resolution failed for {host}: {e}"

        for ip in ip_objs:
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False, f"URL resolves to a private/local IP: {ip}"

        return True, ""
    except Exception as e:
        return False, f"Validation error: {e}"


def web_fetch(url: str, prompt: str | None = None, max_bytes: int = 200_000) -> str:
    """Fetch URL, enforce SSRF guardrails on redirect hops, clean scripts/styles, and convert HTML to Markdown."""
    current_url = url
    max_redirects = 5
    redirect_count = 0

    # We disable follow_redirects in httpx Client, and follow them manually, checking safety at each hop.
    with httpx.Client(follow_redirects=False, timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        while True:
            # Check safety of current URL before fetching
            ok, err = _is_safe_url(current_url)
            if not ok:
                raise ValueError(f"SSRF Check Failed: {err}")

            headers = {"User-Agent": "XHX-Agent/1.0.0"}

            with client.stream("GET", current_url, headers=headers) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    redirect_count += 1
                    if redirect_count > max_redirects:
                        raise RuntimeError("Too many redirects")

                    location = response.headers.get("Location")
                    if not location:
                        raise ValueError(f"Redirect status {response.status_code} without Location header")

                    # Resolve relative URLs
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()

                # Read response body up to max_bytes
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=4096):
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        content = content[:max_bytes]
                        break

                html_text = content.decode("utf-8", errors="replace")

                # Clean up HTML and strip scripts/styles
                soup = BeautifulSoup(html_text, "html.parser")
                for s in soup(["script", "style", "noscript", "iframe", "svg"]):
                    s.decompose()

                cleaned_html = str(soup)
                markdown_text = markdownify(cleaned_html).strip()

                if len(markdown_text) > max_bytes:
                    markdown_text = markdown_text[:max_bytes] + "\n\n[Content Truncated]"

                return markdown_text


def web_search(query: str, api_key: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Perform a web search using the Tavily REST API."""
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "XHX-Agent/1.0.0",
    }

    with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])
