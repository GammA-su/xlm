"""Exact Phase-P URL policy and remote-identity verification.

All decisions are structural (``urllib.parse.urlsplit`` + exact component
comparison); nothing is substring-matched. The frozen policy:

- HTTPS only, port 443 only, no userinfo, no IP literals, no localhost.
- Exact hosts ``huggingface.co`` and ``cas-bridge.xethub.hf.co``; no suffix
  or wildcard matching.
- The canonical resource is the pinned-revision resolve path on
  ``huggingface.co``; its identity is its PATH, never its query string.
- A redirect may go to the signed-target host (opaque path, identity then
  proven by strong ETag + Content-Range total + magic) or back to the exact
  canonical path of the same file. Nothing else.
- A successful identity response is exactly 206 with an exact
  ``bytes s-e/N`` Content-Range, N equal to the frozen length, a strong
  ETag byte-identical to the frozen strong ETag, identity content coding
  and exactly ``e-s+1`` body bytes.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

from xlm.data.evidence_v3 import frozen_v3


class PolicyError(ValueError):
    """URL / redirect policy violation: refuse, never broaden."""


class IdentityError(ValueError):
    """Remote identity mismatch: STOP, never reselect or refresh."""


_STRONG_ETAG = re.compile(r'\A"[\x21\x23-\x7e]*"\Z')
_CONTENT_RANGE = re.compile(r"\Abytes (0|[1-9][0-9]*)-(0|[1-9][0-9]*)/([1-9][0-9]*)\Z")
_REPO_SEGMENT = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._\-]*\Z")
_FILE_SEGMENT = re.compile(r"\A[A-Za-z0-9=_.\-]+\Z")
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True)
class SourceIdentity:
    """Structured frozen source identity (never a URL string)."""

    host: str
    repository_type: str
    repository: str
    revision: str

    def canonical_path(self, file: str) -> str:
        _check_file_path(file)
        return (
            f"/{self.repository_type}/{self.repository}/resolve/"
            f"{quote(self.revision, safe='')}/{quote(file, safe='/=')}"
        )

    def canonical_url(self, file: str) -> str:
        return f"https://{self.host}{self.canonical_path(file)}"


FROZEN_SOURCE = SourceIdentity(
    host=frozen_v3.CANONICAL_HOST,
    repository_type="datasets",
    repository=frozen_v3.SOURCE_REPOSITORY,
    revision=frozen_v3.SOURCE_REVISION,
)


def _check_file_path(file: str) -> None:
    if not isinstance(file, str) or not file or file.startswith("/"):
        raise PolicyError(f"frozen file path must be relative: {file!r}")
    for segment in file.split("/"):
        if segment in ("", ".", "..") or not _FILE_SEGMENT.match(segment):
            raise PolicyError(f"frozen file path has an illegal segment: {file!r}")


def check_source_identity(source: SourceIdentity) -> SourceIdentity:
    if source.host != frozen_v3.CANONICAL_HOST:
        raise PolicyError("canonical host must be huggingface.co")
    if source.repository_type != "datasets":
        raise PolicyError("repository type must be datasets")
    parts = source.repository.split("/")
    if len(parts) != 2 or not all(_REPO_SEGMENT.match(p) for p in parts):
        raise PolicyError("repository must be owner/name")
    if not re.fullmatch(r"[0-9a-f]{40}", source.revision):
        raise PolicyError("revision must be an immutable 40-hex commit")
    return source


@dataclass(frozen=True)
class CheckedUrl:
    """A policy-validated absolute URL (host/port/path split structurally)."""

    url: str
    host: str
    path: str
    has_query: bool


def check_url(url: str) -> CheckedUrl:
    """Validate one absolute URL against the exact frozen policy."""
    if type(url) is not str or not url or len(url) > 8192:
        raise PolicyError("URL must be a non-empty bounded string")
    if any(ord(c) < 0x21 or ord(c) > 0x7E for c in url):
        raise PolicyError("URL must be printable ASCII without spaces")
    if not url.startswith("https://"):
        raise PolicyError("URL must start with the exact lowercase scheme https://")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise PolicyError(f"URL does not parse: {exc}") from exc
    if parts.scheme != "https":
        raise PolicyError(f"scheme {parts.scheme!r} is not https")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise PolicyError("userinfo is forbidden")
    if parts.fragment:
        raise PolicyError("fragments are forbidden")
    host = parts.hostname or ""
    if not host:
        raise PolicyError("URL has no host")
    if host != host.lower() or parts.netloc.split(":")[0] != host:
        raise PolicyError("host must be lowercase and exactly spelled")
    if host == "localhost" or host.endswith(".localhost") or host.endswith("."):
        raise PolicyError("localhost/loopback hosts are forbidden")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise PolicyError("IP literals are forbidden")
    if host not in frozen_v3.ALLOWED_HOSTS:
        raise PolicyError(f"host {host!r} is not an exact allowlisted host")
    if port is not None and port != frozen_v3.ALLOWED_PORT:
        raise PolicyError(f"port {port} is not 443")
    if not parts.path.startswith("/"):
        raise PolicyError("URL path must be absolute")
    return CheckedUrl(url=url, host=host, path=parts.path, has_query=bool(parts.query))


def is_canonical_resource(checked: CheckedUrl, source: SourceIdentity, file: str) -> bool:
    """Structural identity: exact host + exact decoded path; query ignored."""
    if checked.host != source.host:
        return False
    return unquote(checked.path) == unquote(source.canonical_path(file))


def classify_target(checked: CheckedUrl, source: SourceIdentity, file: str) -> str:
    """``canonical`` | ``signed_target``; anything else refuses."""
    if checked.host == frozen_v3.SIGNED_TARGET_HOST:
        return "signed_target"
    if is_canonical_resource(checked, source, file):
        if checked.has_query:
            raise PolicyError("canonical resource must not carry a query string")
        return "canonical"
    raise PolicyError(f"{checked.host}{checked.path} is not the frozen resource for {file}")


def check_redirect(
    location: str | None, *, source: SourceIdentity, file: str, transitions_so_far: int
) -> tuple[CheckedUrl, str]:
    """Validate an actual Location header; enforce the 3-transition ceiling."""
    if transitions_so_far >= frozen_v3.MAX_REDIRECT_TRANSITIONS:
        raise PolicyError(
            f"redirect transition {transitions_so_far + 1} exceeds the "
            f"{frozen_v3.MAX_REDIRECT_TRANSITIONS}-transition ceiling"
        )
    if location is None:
        raise PolicyError("redirect response without a Location header")
    if not location.startswith("https://"):
        raise PolicyError("relative, scheme-relative or non-HTTPS redirects are forbidden")
    checked = check_url(location)
    return checked, classify_target(checked, source, file)


def is_strong_etag(value: object) -> bool:
    return isinstance(value, str) and bool(_STRONG_ETAG.match(value))


def parse_content_range(value: str | None) -> tuple[int, int, int]:
    if value is None:
        raise IdentityError("missing Content-Range")
    match = _CONTENT_RANGE.match(value)
    if match is None:
        raise IdentityError(f"malformed Content-Range {value!r}")
    start, end, total = (int(g) for g in match.groups())
    if not start <= end < total:
        raise IdentityError(f"inconsistent Content-Range {value!r}")
    return start, end, total


@dataclass(frozen=True)
class ExpectedIdentity:
    """What an authorized plan operation requires of its final response."""

    start: int
    end: int
    total_length: int
    strong_etag: str
    magic: str  # "head" | "tail" | "none"


def verify_identity(
    *,
    status: int,
    headers: Mapping[str, str],
    body: bytes,
    final_url: CheckedUrl,
    final_kind: str,
    source: SourceIdentity,
    file: str,
    expected: ExpectedIdentity,
) -> None:
    """Exact identity for one successful range response; mismatch is STOP."""
    if not is_strong_etag(expected.strong_etag):
        raise IdentityError("the frozen expected ETag is not a strong ETag")
    if status != 206:
        raise IdentityError(f"status {status} is not 206")
    if final_kind == "canonical":
        if not is_canonical_resource(final_url, source, file):
            raise IdentityError("final resource path is not the frozen resource")
    elif final_kind != "signed_target" or final_url.host != frozen_v3.SIGNED_TARGET_HOST:
        raise IdentityError("final resource is neither canonical nor the signed target host")
    start, end, total = parse_content_range(headers.get("content-range"))
    if (start, end) != (expected.start, expected.end):
        raise IdentityError(
            f"Content-Range {start}-{end} != requested {expected.start}-{expected.end}"
        )
    if total != expected.total_length:
        raise IdentityError(f"Content-Range total {total} != frozen length {expected.total_length}")
    etag = headers.get("etag")
    if not is_strong_etag(etag):
        raise IdentityError("response ETag is missing or weak")
    if etag != expected.strong_etag:
        raise IdentityError("response ETag differs from the frozen strong ETag")
    encoding = headers.get("content-encoding")
    if encoding not in (None, "identity"):
        raise IdentityError(f"content-encoding {encoding!r} is not identity")
    length = expected.end - expected.start + 1
    declared = headers.get("content-length")
    if declared is not None and declared != str(length):
        raise IdentityError(f"Content-Length {declared!r} != range length {length}")
    if len(body) != length:
        raise IdentityError(f"body has {len(body)} bytes, range requires exactly {length}")
    if expected.magic == "head" and body[:4] != b"PAR1":
        raise IdentityError("PAR1 header magic missing")
    if expected.magic == "tail" and body[-4:] != b"PAR1":
        raise IdentityError("PAR1 trailer magic missing")
