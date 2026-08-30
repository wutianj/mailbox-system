from __future__ import annotations

import argparse
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path("/opt/mailbox-system")
API = "http://127.0.0.1:18080"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export active mailbox API links.")
    parser.add_argument("--domain", default="i7wap.xyz")
    args = parser.parse_args()

    token = read_env(ROOT / "mailbox-api.env")["ADMIN_API_KEY"]
    query = urllib.parse.urlencode({"domain": args.domain})
    req = urllib.request.Request(API + "/admin/export?" + query, headers={"X-Admin-Token": token})
    with urllib.request.urlopen(req, timeout=60) as response:
        text = response.read().decode("utf-8", errors="replace")

    export_dir = ROOT / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    export_path = export_dir / f"{stamp}-{args.domain}-active.txt"
    export_path.write_text(text + ("\n" if text and not text.endswith("\n") else ""), encoding="utf-8")
    export_path.chmod(0o600)
    count = len([line for line in text.splitlines() if line.strip()])
    print(f"count={count}")
    print(f"export_path={export_path}")
    return 0


def read_env(path: Path) -> dict[str, str]:
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value
    return data


if __name__ == "__main__":
    raise SystemExit(main())
