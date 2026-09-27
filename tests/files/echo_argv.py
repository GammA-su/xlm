"""Echo argv as JSON plus ASCII verification lines (offline driver tests).

The COUNT/SHA256 lines are pure ASCII so host console encodings (e.g.
cp932) and PowerShell 5.1 JSON decoding quirks can never corrupt the
verdict: exactness is decided by the digest over NUL-joined UTF-8 argv.
"""

from __future__ import annotations

import hashlib
import json
import sys


def main() -> int:
    payload = sys.argv[1:]
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))
    sys.stdout.write("\n")
    sys.stdout.write(f"COUNT:{len(payload)}\n")
    digest = hashlib.sha256("\x00".join(payload).encode("utf-8")).hexdigest()
    sys.stdout.write(f"SHA256:{digest}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
