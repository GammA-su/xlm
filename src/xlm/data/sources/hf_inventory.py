"""Generic bounded Hugging Face repository inventory/listing (offline-first).

Live metadata listing (provider enumeration only) is distinct from the
inventory freeze (deterministic local transformation into XLM production
inventory). This module never fetches corpus payload: only repository
tree/file metadata endpoints (``/api/datasets/.../tree/...``) are allowed.
``/resolve/`` URLs, range reads and Parquet footers are refused.

Offline pure logic (:func:`collect_listing`) drives pagination, filtering and
digests from an injected page fetcher, so tests use authored fixtures or
isolated loopback HTTP only. Live transport (:class:`HfTreeFetcher`) is a
thin metadata-only wrapper used solely by the future operator listing.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

LISTING_KIND = "hf_repository_file_listing"
LISTING_VERSION = 1
PROVIDER = "huggingface"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
MIB = 1024**2
GIB = 1024**3
MAX_DECLARED_FILE_BYTES = 64 * GIB
MAX_PATH_LENGTH = 1024
OID_MAX_LENGTH = 256

DEFAULT_MAX_PAGES = 128
DEFAULT_MAX_ITEMS = 50000
DEFAULT_MAX_REQUESTS = 256
DEFAULT_MAX_METADATA_BYTES = 16 * MIB
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_DEADLINE_SECONDS = 300.0


class HfInventoryError(ValueError):
    """A listing input or page is missing, inconsistent or unsafe; nothing is published."""


class TransientFetchError(OSError):
    """One page fetch failed transiently and may be retried within budget."""


@dataclass(frozen=True)
class ListingFilter:
    """Generic file-selection policy; no source is hard-coded here."""

    path_prefix: str = ""
    extension_allowlist: tuple[str, ...] = ()
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "path_prefix": self.path_prefix,
            "extension_allowlist": sorted(self.extension_allowlist),
            "include_globs": sorted(self.include_globs),
            "exclude_globs": sorted(self.exclude_globs),
        }


@dataclass(frozen=True)
class ListingLimits:
    """Explicit safety ceilings; pagination fails closed when any is reached."""

    max_pages: int = DEFAULT_MAX_PAGES
    max_items: int = DEFAULT_MAX_ITEMS
    max_requests: int = DEFAULT_MAX_REQUESTS
    max_metadata_bytes: int = DEFAULT_MAX_METADATA_BYTES
    max_retries: int = DEFAULT_MAX_RETRIES
    per_request_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    total_deadline_seconds: float = DEFAULT_DEADLINE_SECONDS


@dataclass(frozen=True)
class PageItem:
    """One provider-declared file entry before filtering."""

    path: str
    size_bytes: int | None
    oid: str | None = None


@dataclass(frozen=True)
class TreePage:
    """One metadata page from the provider."""

    items: tuple[PageItem, ...]
    next_cursor: str | None
    page_revision: str | None = None
    total_declared: int | None = None
    raw_bytes: int = 0


class PageFetcher(Protocol):
    """Injected page source; offline tests use authored fixtures."""

    def fetch(self, cursor: str | None) -> TreePage: ...


def check_revision(value: str, what: str) -> str:
    text = (value or "").strip()
    if not SHA_RE.fullmatch(text):
        raise HfInventoryError(f"{what} must be an exact 40-hex commit SHA, got {value!r}")
    return text


def check_repository(value: str) -> str:
    text = (value or "").strip()
    if not REPO_RE.fullmatch(text):
        raise HfInventoryError(f"repository must be 'owner/name', got {value!r}")
    return text


def check_name(value: str, what: str) -> str:
    text = (value or "").strip()
    if not text or not NAME_RE.fullmatch(text.replace("-", "_").replace(".", "_")):
        # source/view ids allow letters, digits, underscore, dash, dot
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", text):
            raise HfInventoryError(f"{what} must be a non-empty id, got {value!r}")
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", text):
        raise HfInventoryError(f"{what} must be a non-empty id, got {value!r}")
    return text


def normalize_filter(
    *,
    path_prefix: str = "",
    extensions: tuple[str, ...] | list[str] = (),
    include_globs: tuple[str, ...] | list[str] = (),
    exclude_globs: tuple[str, ...] | list[str] = (),
) -> ListingFilter:
    prefix = (path_prefix or "").strip().replace("\\", "/")
    if prefix.startswith("/") or ".." in prefix.split("/"):
        raise HfInventoryError(f"path prefix is not a relative posix prefix: {path_prefix!r}")
    # Collapse duplicate slashes; keep a single trailing slash for non-empty prefixes.
    parts = [p for p in prefix.split("/") if p]
    if prefix and not parts:
        raise HfInventoryError(f"path prefix is not a relative posix prefix: {path_prefix!r}")
    for part in parts:
        if part in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.\-=+,%{}@]+", part):
            # Allow typical dataset path characters; reject traversal/control.
            if part in (".", ".."):
                raise HfInventoryError(f"path prefix escapes its root: {path_prefix!r}")
    normalized_prefix = ("/".join(parts) + "/") if parts else ""
    exts: list[str] = []
    for ext in extensions:
        e = str(ext).strip().lower()
        if not e.startswith(".") or len(e) < 2 or len(e) > 16 or " " in e or "/" in e:
            raise HfInventoryError(f"extension allowlist entry must be like '.parquet': {ext!r}")
        exts.append(e)
    for label, globs in (("include", include_globs), ("exclude", exclude_globs)):
        for pattern in globs:
            p = str(pattern)
            if not p or p.startswith("/") or ".." in p.split("/") or "\\" in p:
                raise HfInventoryError(f"{label} glob must be a relative posix glob: {pattern!r}")
    return ListingFilter(
        path_prefix=normalized_prefix,
        extension_allowlist=tuple(sorted(set(exts))),
        include_globs=tuple(sorted(set(str(g) for g in include_globs))),
        exclude_globs=tuple(sorted(set(str(g) for g in exclude_globs))),
    )


def check_path(path: str) -> str:
    """Validate and normalize one provider-declared relative posix path."""
    if not isinstance(path, str) or not path:
        raise HfInventoryError(f"file path must be a non-empty string, got {path!r}")
    if len(path) > MAX_PATH_LENGTH:
        raise HfInventoryError(f"file path exceeds {MAX_PATH_LENGTH} chars: {path!r}")
    if "\\" in path or "\x00" in path or "\r" in path or "\n" in path:
        raise HfInventoryError(f"file path carries forbidden characters: {path!r}")
    if path.startswith("/") or "//" in path or path.endswith("/"):
        raise HfInventoryError(f"file path is not a relative file path: {path!r}")
    parts = path.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise HfInventoryError(f"file path escapes or is malformed: {path!r}")
        if part != part.strip():
            raise HfInventoryError(f"file path has surrounding whitespace: {path!r}")
    for ch in path:
        if ord(ch) < 32 or ord(ch) == 127:
            raise HfInventoryError(f"file path carries control characters: {path!r}")
    return path


def check_size(size: int | None, path: str) -> int | None:
    if size is None:
        return None
    if isinstance(size, bool) or not isinstance(size, int):
        raise HfInventoryError(f"size of {path!r} must be a non-negative integer or null")
    if size < 0 or size > MAX_DECLARED_FILE_BYTES:
        raise HfInventoryError(f"size of {path!r} is out of bounds: {size}")
    return size


def check_oid(oid: str | None, path: str) -> str | None:
    if oid is None:
        return None
    if not isinstance(oid, str) or not oid.strip():
        raise HfInventoryError(f"identity of {path!r} must be a non-empty string or null")
    text = oid.strip()
    if len(text) > OID_MAX_LENGTH or any(ord(c) < 33 or ord(c) == 127 for c in text):
        raise HfInventoryError(f"identity of {path!r} is malformed")
    return text


def matches_filter(path: str, filt: ListingFilter) -> bool:
    if filt.path_prefix and not path.startswith(filt.path_prefix):
        return False
    if filt.extension_allowlist:
        lowered = path.lower()
        if not any(lowered.endswith(ext) for ext in filt.extension_allowlist):
            return False
    for pattern in filt.include_globs:
        if not fnmatch.fnmatch(path, pattern):
            return False
    for pattern in filt.exclude_globs:
        if fnmatch.fnmatch(path, pattern):
            return False
    return True


def check_limits(limits: ListingLimits) -> ListingLimits:
    int_limits: tuple[tuple[str, int, int], ...] = (
        ("max_pages", limits.max_pages, 1),
        ("max_items", limits.max_items, 1),
        ("max_requests", limits.max_requests, 1),
        ("max_metadata_bytes", limits.max_metadata_bytes, 1),
        ("max_retries", limits.max_retries, 0),
    )
    for name, value, minimum in int_limits:
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise HfInventoryError(f"limit {name} must be an integer >= {minimum}")
    float_limits: tuple[tuple[str, float], ...] = (
        ("per_request_timeout_seconds", float(limits.per_request_timeout_seconds)),
        ("total_deadline_seconds", float(limits.total_deadline_seconds)),
    )
    for fname, fvalue in float_limits:
        if isinstance(fvalue, bool) or not isinstance(fvalue, (int, float)) or fvalue <= 0:
            raise HfInventoryError(f"limit {fname} must be a positive number")
    return limits


def _file_list_digest(entries: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        entries, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _self_digest(body: dict[str, Any]) -> str:
    canonical = json.dumps(
        {k: v for k, v in body.items() if k != "digest"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def collect_listing(
    *,
    repository: str,
    requested_revision: str,
    source_id: str,
    view_id: str,
    filt: ListingFilter,
    limits: ListingLimits,
    fetcher: PageFetcher,
    resolved_revision: str | None = None,
) -> dict[str, Any]:
    """Enumerate every matching file at the pinned revision; fail closed on any bound.

    The result is a digested listing receipt (live metadata listing). It is
    deterministic: files are sorted ascending by path regardless of provider
    page order, and digests cover the exact revision, repository, source/view
    and filters.
    """
    repo = check_repository(repository)
    requested = check_revision(requested_revision, "requested revision")
    if resolved_revision:
        resolved = check_revision(resolved_revision, "resolved revision")
    else:
        resolved = requested
    if resolved != requested:
        raise HfInventoryError("resolved revision differs from the requested pinned revision")
    check_name(source_id, "source_id")
    check_name(view_id, "view_id")
    check_limits(limits)
    if not isinstance(filt, ListingFilter):
        raise HfInventoryError("filter must be a ListingFilter")

    deadline = time.monotonic() + float(limits.total_deadline_seconds)
    collected: dict[str, dict[str, Any]] = {}
    seen_cursors: set[str | None] = set()
    cursors_observed: list[str] = []
    page_count = 0
    request_count = 0
    metadata_bytes = 0
    cursor: str | None = None
    last_total: int | None = None

    while True:
        if time.monotonic() > deadline:
            raise HfInventoryError("listing deadline exceeded; refusing an incomplete inventory")
        if page_count >= limits.max_pages:
            raise HfInventoryError(
                f"page ceiling reached ({limits.max_pages}); refusing an incomplete inventory"
            )
        if request_count >= limits.max_requests:
            raise HfInventoryError(
                f"request ceiling reached ({limits.max_requests}); refusing an incomplete inventory"
            )
        if cursor in seen_cursors and page_count > 0 and cursor is not None:
            raise HfInventoryError(f"pagination loop: cursor {cursor!r} was already visited")
        seen_cursors.add(cursor)

        attempts = 0
        page: TreePage | None = None
        while True:
            if time.monotonic() > deadline:
                raise HfInventoryError(
                    "listing deadline exceeded; refusing an incomplete inventory"
                )
            if request_count >= limits.max_requests:
                raise HfInventoryError(
                    f"request ceiling reached ({limits.max_requests}); "
                    "refusing an incomplete inventory"
                )
            try:
                request_count += 1
                page = fetcher.fetch(cursor)
                break
            except TransientFetchError:
                attempts += 1
                if attempts > limits.max_retries:
                    raise HfInventoryError(
                        f"page fetch failed after {attempts} attempt(s); "
                        "refusing an incomplete inventory"
                    ) from None
                continue
            except HfInventoryError:
                raise
            except Exception as exc:
                raise HfInventoryError(f"page fetch failed: {type(exc).__name__}: {exc}") from exc
        assert page is not None
        metadata_bytes += int(page.raw_bytes or 0)
        if metadata_bytes > limits.max_metadata_bytes:
            raise HfInventoryError(
                f"metadata byte ceiling reached ({limits.max_metadata_bytes}); "
                "refusing an incomplete inventory"
            )
        if page.page_revision is not None:
            rev = check_revision(str(page.page_revision), "page revision")
            if rev != resolved:
                raise HfInventoryError(
                    f"page revision {rev} differs from the pinned revision {resolved}"
                )
        if page.next_cursor is not None and not isinstance(page.next_cursor, str):
            raise HfInventoryError("pagination cursor must be a string or null")
        if page.next_cursor == cursor and cursor is not None:
            raise HfInventoryError("pagination loop: provider returned the same cursor")
        if page.total_declared is not None:
            if isinstance(page.total_declared, bool) or not isinstance(page.total_declared, int):
                raise HfInventoryError("page total must be an integer or null")
            if page.total_declared < 0:
                raise HfInventoryError("page total is negative")
            last_total = page.total_declared
        if not isinstance(page.items, (list, tuple)):
            raise HfInventoryError("page items must be a list")
        if len(collected) + len(page.items) > limits.max_items + 100000:
            # Hard backstop against pathological pages before per-item accounting.
            raise HfInventoryError(
                "item ceiling would be exceeded; refusing an incomplete inventory"
            )
        for item in page.items:
            if not isinstance(item, PageItem):
                raise HfInventoryError("page item must carry path/size/identity")
            path = check_path(item.path)
            size = check_size(item.size_bytes, path)
            oid = check_oid(item.oid, path)
            if path in collected:
                prev = collected[path]
                if prev["size_bytes"] != size or (prev.get("oid") or None) != (oid or None):
                    raise HfInventoryError(f"duplicate path with conflicting metadata: {path!r}")
                raise HfInventoryError(f"duplicate path in listing: {path!r}")
            if len(collected) >= limits.max_items and matches_filter(path, filt):
                raise HfInventoryError(
                    f"item ceiling reached ({limits.max_items}); refusing an incomplete inventory"
                )
            if not matches_filter(path, filt):
                continue
            collected[path] = {"path": path, "size_bytes": size, "oid": oid}
        page_count += 1
        if cursor is not None:
            cursors_observed.append(cursor)
        if page.next_cursor is None:
            if last_total is not None and len(collected) > last_total:
                raise HfInventoryError("collected more files than the provider-declared total")
            # Missing-continuation guard when the provider declares a larger
            # universe than what pagination delivered.
            if last_total is not None and len(collected) < last_total:
                # Filtered listings legitimately collect fewer than the raw
                # total; only fail when the fetcher claims the count is for
                # the *filtered* set. By convention a filtered total must equal
                # the delivered filtered count at completion.
                raise HfInventoryError(
                    "pagination ended but the provider-declared total is larger; "
                    "refusing a silently truncated inventory"
                )
            break
        if page.next_cursor in seen_cursors:
            raise HfInventoryError(
                f"pagination loop: next cursor {page.next_cursor!r} was already visited"
            )
        cursor = page.next_cursor

    ordered = [collected[k] for k in sorted(collected)]
    # Normalize oid omission for determinism: explicit null stays null.
    entries = [
        {"path": e["path"], "size_bytes": e["size_bytes"], "oid": e.get("oid")} for e in ordered
    ]
    known = [int(e["size_bytes"]) for e in entries if e["size_bytes"] is not None]
    unknown = sum(1 for e in entries if e["size_bytes"] is None)
    file_digest = _file_list_digest(entries)
    body: dict[str, Any] = {
        "kind": LISTING_KIND,
        "listing_version": LISTING_VERSION,
        "provider": PROVIDER,
        "repository": repo,
        "requested_revision": requested,
        "resolved_revision": resolved,
        "source_id": source_id,
        "view_id": view_id,
        "filters": filt.as_dict(),
        "files": entries,
        "item_count": len(entries),
        "total_declared_bytes": sum(known) if unknown == 0 else None,
        "known_size_bytes": sum(known),
        "unknown_size_count": unknown,
        "page_count": page_count,
        "request_count": request_count,
        "metadata_bytes": metadata_bytes,
        "cursors_observed": sorted(cursors_observed),
        "pagination_complete": True,
        "completion_status": "complete",
        "provider_total_declared": last_total,
        "file_list_digest": file_digest,
    }
    body["digest"] = _self_digest(body)
    return body


def verify_listing(receipt: dict[str, Any]) -> dict[str, Any]:
    """Offline verification of a listing receipt; raises on any mismatch."""
    if not isinstance(receipt, dict):
        raise HfInventoryError("listing receipt must be a JSON object")
    if receipt.get("kind") != LISTING_KIND:
        raise HfInventoryError("not a Hugging Face repository listing receipt")
    if receipt.get("listing_version") != LISTING_VERSION:
        raise HfInventoryError("unsupported listing version")
    if receipt.get("provider") != PROVIDER:
        raise HfInventoryError("listing provider must be 'huggingface'")
    repo = check_repository(str(receipt.get("repository", "")))
    requested = check_revision(str(receipt.get("requested_revision", "")), "requested revision")
    resolved = check_revision(str(receipt.get("resolved_revision", "")), "resolved revision")
    if resolved != requested:
        raise HfInventoryError("listing resolved revision differs from the requested pin")
    check_name(str(receipt.get("source_id", "")), "source_id")
    check_name(str(receipt.get("view_id", "")), "view_id")
    filters = receipt.get("filters")
    if not isinstance(filters, dict):
        raise HfInventoryError("listing filters must be an object")
    filt = normalize_filter(
        path_prefix=str(filters.get("path_prefix", "")),
        extensions=tuple(filters.get("extension_allowlist", ())),
        include_globs=tuple(filters.get("include_globs", ())),
        exclude_globs=tuple(filters.get("exclude_globs", ())),
    )
    files = receipt.get("files")
    if not isinstance(files, list) or not files:
        raise HfInventoryError("listing holds no file; refusing an empty candidate universe")
    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "size_bytes", "oid"}:
            raise HfInventoryError("listing file entry must hold exactly path/size_bytes/oid")
        path = check_path(str(entry["path"]))
        size = check_size(entry["size_bytes"], path)
        oid = check_oid(entry.get("oid"), path)
        if path in seen:
            raise HfInventoryError(f"duplicate path in listing: {path!r}")
        seen.add(path)
        if not matches_filter(path, filt):
            raise HfInventoryError(f"listing file {path!r} violates its own filters")
        entries.append({"path": path, "size_bytes": size, "oid": oid})
    ordered_paths = [e["path"] for e in entries]
    if ordered_paths != sorted(ordered_paths):
        raise HfInventoryError("listing files are not sorted ascending by path")
    if receipt.get("item_count") != len(entries):
        raise HfInventoryError("listing item count does not match its files")
    known = [int(e["size_bytes"]) for e in entries if e["size_bytes"] is not None]
    unknown = sum(1 for e in entries if e["size_bytes"] is None)
    expected_total = sum(known) if unknown == 0 else None
    if receipt.get("total_declared_bytes") != expected_total:
        raise HfInventoryError("listing total bytes do not match its files")
    if receipt.get("known_size_bytes") != sum(known):
        raise HfInventoryError("listing known bytes do not match its files")
    if receipt.get("unknown_size_count") != unknown:
        raise HfInventoryError("listing unknown-size count does not match its files")
    if receipt.get("file_list_digest") != _file_list_digest(entries):
        raise HfInventoryError("listing file-list digest does not verify")
    if receipt.get("pagination_complete") is not True:
        raise HfInventoryError("listing pagination did not complete; refusing")
    if receipt.get("completion_status") != "complete":
        raise HfInventoryError("listing completion status is not 'complete'; refusing")
    for key in ("page_count", "request_count", "metadata_bytes"):
        value = receipt.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HfInventoryError(f"listing {key} must be a non-negative integer")
    if int(receipt["page_count"]) < 1 or int(receipt["request_count"]) < 1:
        raise HfInventoryError("listing page/request counts are not positive")
    body = dict(receipt)
    digest = body.pop("digest", None)
    if not isinstance(digest, str) or digest != _self_digest(body):
        raise HfInventoryError("listing digest does not verify")
    return {
        "repository": repo,
        "revision": resolved,
        "item_count": len(entries),
        "total_declared_bytes": expected_total,
        "file_list_digest": receipt["file_list_digest"],
        "digest": digest,
    }


def listing_to_freeze_inputs(receipt: dict[str, Any]) -> tuple[list[str], dict[str, int]]:
    """Verified listing files/sizes for the existing inventory freeze."""
    summary = verify_listing(receipt)
    _ = summary
    files = receipt["files"]
    names = [str(e["path"]) for e in files]
    sizes: dict[str, int] = {}
    for entry in files:
        if entry["size_bytes"] is not None:
            sizes[str(entry["path"])] = int(entry["size_bytes"])
    if not names or len(set(names)) != len(names):
        raise HfInventoryError("listing cannot freeze an empty or duplicated inventory")
    return names, sizes


def write_listing(path: Path, receipt: dict[str, Any]) -> None:
    """Atomic write-once store; an identical receipt is reused, a divergent one refused."""
    verify_listing(receipt)
    rendered = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        existing = path.read_bytes()
        if existing == rendered:
            return
        raise HfInventoryError(
            f"existing listing '{path}' differs from the new receipt; refusing to overwrite it"
        )
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered.decode("utf-8"))
    tmp.replace(path)


def read_listing(path: Path, *, max_bytes: int = 64 * MIB) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise HfInventoryError(f"listing '{path}' is missing: {exc}") from exc
    if size > max_bytes:
        raise HfInventoryError(f"listing '{path}' exceeds {max_bytes} bytes")
    try:
        loaded: Any = json.loads(path.read_bytes().decode("utf-8"))
    except Exception as exc:
        raise HfInventoryError(f"listing '{path}' is unreadable: {exc}") from exc
    if not isinstance(loaded, dict):
        raise HfInventoryError(f"listing '{path}' is not a JSON object")
    payload: dict[str, Any] = dict(loaded)
    verify_listing(payload)
    return payload


# ---------------------------------------------------------------- live fetch
#: Only repository tree metadata endpoints may be fetched. Any corpus blob
#: (``/resolve/``), Parquet footer range or content URL is refused.
ALLOWED_TREE_PREFIXES: tuple[str, ...] = ("/api/datasets/",)


def build_tree_url(
    *,
    base_url: str = "https://huggingface.co",
    repository: str,
    revision: str,
    path_prefix: str = "",
    cursor: str | None = None,
    recursive: bool = True,
) -> str:
    repo = check_repository(repository)
    rev = check_revision(revision, "revision")
    if base_url.startswith("http://"):
        host = (urllib.parse.urlparse(base_url).hostname or "").lower()
        if host not in ("127.0.0.1", "localhost"):
            raise HfInventoryError("cleartext HTTP is restricted to loopback fixtures")
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise HfInventoryError("base URL must be HTTP(S)")
    prefix = (path_prefix or "").strip().replace("\\", "/")
    if prefix.startswith("/") or ".." in [p for p in prefix.split("/") if p]:
        raise HfInventoryError("tree path prefix escapes its root")
    enc_repo = urllib.parse.quote(repo, safe="")
    # Preserve the slash between owner and name: quote each segment.
    owner, name = repo.split("/", 1)
    enc_repo = f"{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"
    enc_rev = urllib.parse.quote(rev, safe="")
    # Path inside the repo is appended verbatim (quoted per segment).
    suffix = "/".join(urllib.parse.quote(p, safe="") for p in prefix.split("/") if p)
    url = f"{base_url.rstrip('/')}/api/datasets/{enc_repo}/tree/{enc_rev}"
    if suffix:
        url += f"/{suffix}"
    query: dict[str, str] = {}
    if recursive:
        query["recursive"] = "True"
    if cursor:
        query["cursor"] = cursor
    if query:
        url += "?" + urllib.parse.urlencode(query)
    if "/resolve/" in url:
        raise HfInventoryError("tree URL must never address a corpus blob")
    return url


def assert_metadata_only(url: str) -> None:
    """Refuse any endpoint that could return corpus payload."""
    lowered = url.lower()
    for forbidden in ("/resolve/", "/raw/", ".parquet?", ".parquet#", "range=", "content-range"):
        if forbidden in lowered:
            raise HfInventoryError(f"listing must never fetch corpus payload: {url!r}")
    parsed = urllib.parse.urlparse(url)
    if "/api/datasets/" not in parsed.path or "/tree/" not in parsed.path:
        raise HfInventoryError(f"listing allows only repository tree metadata endpoints: {url!r}")


@dataclass
class HfTreeFetcher:
    """Bounded metadata-only Hub tree fetcher (live operator path only)."""

    repository: str
    revision: str
    path_prefix: str = ""
    recursive: bool = True
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = 4 * MIB
    base_url: str = "https://huggingface.co"
    opener_factory: Any = None
    _requests: int = field(default=0, init=False, repr=False)

    def _opener(self) -> Any:
        if self.opener_factory is not None:
            return self.opener_factory()
        return urllib.request.build_opener()

    def fetch(self, cursor: str | None) -> TreePage:
        from xlm.data.sources.transport import validate_host

        url = build_tree_url(
            base_url=self.base_url,
            repository=self.repository,
            revision=self.revision,
            path_prefix=self.path_prefix,
            cursor=cursor,
            recursive=self.recursive,
        )
        assert_metadata_only(url)
        validate_host(url)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "xlm-inventory/1", "Accept": "application/json"},
            method="GET",
        )
        opener = self._opener()
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise TransientFetchError(f"tree endpoint returned HTTP {status}")
                headers = response.headers or {}
                # Revision consistency: refuse silently serving another commit.
                served = headers.get("X-Repo-Commit") or headers.get("X-Linked-Commit")
                if served is not None and str(served).strip() not in ("", self.revision):
                    raise HfInventoryError("provider served tree metadata from a different commit")
                length = headers.get("Content-Length")
                if length is not None:
                    try:
                        if int(length) > self.max_response_bytes:
                            raise HfInventoryError("tree page exceeds its metadata byte limit")
                    except ValueError:
                        pass
                body = response.read(self.max_response_bytes + 1)
                if len(body) > self.max_response_bytes:
                    raise HfInventoryError("tree page exceeds its metadata byte limit")
                raw = len(body)
                try:
                    items_raw = json.loads(body.decode("utf-8"))
                except Exception as exc:
                    raise HfInventoryError(f"tree page is not JSON: {exc}") from exc
                if not isinstance(items_raw, list):
                    raise HfInventoryError("tree page is not a JSON array")
                items: list[PageItem] = []
                for entry in items_raw:
                    if not isinstance(entry, dict) or entry.get("type") not in (
                        "file",
                        "lfs",
                    ):
                        continue
                    path = str(entry.get("path", ""))
                    size = entry.get("size")
                    oid = entry.get("oid") or entry.get("lfs", {}).get("oid")
                    if isinstance(oid, dict):
                        oid = None
                    items.append(
                        PageItem(
                            path=path,
                            size_bytes=size if size is None or type(size) is int else -1,
                            oid=str(oid) if oid is not None else None,
                        )
                    )
                next_cursor: str | None = None
                link = headers.get("Link", "")
                if 'rel="next"' in link:
                    # Link: <...?cursor=XYZ>; rel="next"
                    match = re.search(r"[?&]cursor=([^&<>; ]+)", link)
                    if match:
                        next_cursor = urllib.parse.unquote(match.group(1))
                # A JSON envelope carrying pagination is also honored.
                if isinstance(items_raw, dict):  # pragma: no cover - defensive
                    nxt = items_raw.get("next_cursor")
                    if isinstance(nxt, str) and nxt:
                        next_cursor = nxt
                self._requests += 1
                return TreePage(
                    items=tuple(items),
                    next_cursor=next_cursor,
                    page_revision=str(served).strip() if served else self.revision,
                    total_declared=None,
                    raw_bytes=raw,
                )
        except HfInventoryError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                raise TransientFetchError(f"tree endpoint HTTP {exc.code}") from exc
            raise HfInventoryError(f"tree endpoint HTTP {exc.code}") from exc
        except (TimeoutError, OSError) as exc:
            raise TransientFetchError(f"tree fetch failed: {type(exc).__name__}") from exc


__all__ = [
    "ALLOWED_TREE_PREFIXES",
    "DEFAULT_DEADLINE_SECONDS",
    "DEFAULT_MAX_ITEMS",
    "DEFAULT_MAX_METADATA_BYTES",
    "DEFAULT_MAX_PAGES",
    "DEFAULT_MAX_REQUESTS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT_SECONDS",
    "HfInventoryError",
    "HfTreeFetcher",
    "LISTING_KIND",
    "LISTING_VERSION",
    "ListingFilter",
    "ListingLimits",
    "MAX_DECLARED_FILE_BYTES",
    "PageFetcher",
    "PageItem",
    "PROVIDER",
    "TransientFetchError",
    "TreePage",
    "assert_metadata_only",
    "build_tree_url",
    "check_limits",
    "check_oid",
    "check_path",
    "check_repository",
    "check_revision",
    "check_size",
    "collect_listing",
    "listing_to_freeze_inputs",
    "matches_filter",
    "normalize_filter",
    "read_listing",
    "verify_listing",
    "write_listing",
]
