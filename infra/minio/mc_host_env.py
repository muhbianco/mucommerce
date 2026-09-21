"""Build `MC_HOST_<alias>=http://<user>:<pass>@minio:9000` from a MinIO root env file.

Used by setup.sh. Reads ROOT_ENV_FILE (MINIO_ROOT_USER / MINIO_ROOT_PASSWORD) and prints the mc
alias line to stdout, which setup.sh redirects into a 0600 temp file. `--user` prints only the
root user name (needed as the parent of the service account).
"""

from __future__ import annotations

import os
import sys
from urllib.parse import quote


def read_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    values = read_env(os.environ["ROOT_ENV_FILE"])
    user = values.get("MINIO_ROOT_USER", "")
    password = values.get("MINIO_ROOT_PASSWORD", "")
    if not user or not password:
        print("MINIO_ROOT_USER / MINIO_ROOT_PASSWORD missing", file=sys.stderr)
        return 2
    if "--user" in sys.argv[1:]:
        print(user)
        return 0
    alias = os.environ.get("ALIAS", "hel1")
    print(f"MC_HOST_{alias}=http://{quote(user, safe='')}:{quote(password, safe='')}@minio:9000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
