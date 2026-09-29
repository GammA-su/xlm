"""Strict v3 Phase-P transport policy (exact allowlist, no broadening).

Independent probes showed the old host validation accepted HTTP loopback,
IP literals, raw.githubusercontent.com, arbitrary *.hf.co, and HTTPS port
444. This module enforces the frozen policy exactly:

- HTTPS only, port 443 only (default or explicit)
- exact frozen allowlisted hosts only (no suffix matching)
- no IP literals, no localhost/loopback, no userinfo
- every redirect hop revalidated; at most 3 redirect transitions per
  logical attempt
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

from xlm.data.evidence_v3 import frozen_v3


class TransportPolicyError(ValueError):
    """Any transport-policy violation: refuse."""


FROZEN_HOSTS = ("huggingface.co", "cas-bridge.xethub.hf.co")
MAX_REDIRECT_TRANSITIONS = 3


def _reject(message: str) -> TransportPolicyError:
    return TransportPolicyError(message)


def validate_url(url: str) -> dict[str, Any]:
    """Validate one URL against the exact frozen policy."""
    if not isinstance(url, str) or not url:
        raise _reject("URL must be a non-empty string")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise _reject(f"URL does not parse: {exc}") from exc
    if parts.scheme != "https":
        raise _reject(f"scheme {parts.scheme!r} is not https")
    if "@" in parts.netloc:
        raise _reject("userinfo in URL is forbidden")
    host = (parts.hostname or "").lower()
    if not host:
        raise _reject("URL has no host")
    if host in ("localhost",) or host.endswith(".localhost"):
        raise _reject("localhost/loopback is forbidden")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise _reject("IP literals are forbidden")
    if host not in FROZEN_HOSTS:
        raise _reject(f"host {host!r} is not in the frozen allowlist")
    port = parts.port
    if port is not None and port != 443:
        raise _reject(f"port {port} is not 443")
    if not parts.path or not parts.path.startswith("/"):
        raise _reject("URL path must be absolute")
    return {"scheme": "https", "host": host, "port": 443, "url": url}


def validate_redirect_chain(urls: list[str]) -> dict[str, Any]:
    """Validate an original URL plus its redirect hops.

    ``urls[0]`` is the requested URL; each subsequent entry is a redirect
    target actually followed. Every hop must pass the exact policy and the
    chain may contain at most 3 redirect transitions.
    """
    if not isinstance(urls, list) or not urls:
        raise _reject("redirect chain must be a non-empty list")
    checked = [validate_url(url) for url in urls]
    transitions = len(urls) - 1
    if transitions > MAX_REDIRECT_TRANSITIONS:
        raise _reject(f"{transitions} redirect transitions exceed the cap of 3")
    return {"hops": checked, "redirect_transitions": transitions}


def check_frozen_hosts_match_protocol() -> None:
    """The module allowlist must equal the frozen protocol host set."""
    if tuple(FROZEN_HOSTS) != ("huggingface.co", "cas-bridge.xethub.hf.co"):
        raise _reject("transport allowlist drifted from the frozen protocol")
    if MAX_REDIRECT_TRANSITIONS != frozen_v3.ARM_M_CAPS["max_redirect_hops"]:
        raise _reject("redirect cap drifted from the frozen protocol")
