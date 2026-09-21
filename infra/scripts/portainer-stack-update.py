#!/usr/bin/env python3
"""Portainer StackUpdate from hel1 without secrets leaving the host.

Portainer replaces the whole stack Env on update, and the MCP only sees masked values, so a
stack with `${VAR}` secrets (commerce) has to be updated from the host with the API token.

  portainer-stack-update.py --stack commerce --yaml infra/docker-stack.yml       --set-env COMMERCE_TAG=<sha> [--prune] [--dry-run]
  portainer-stack-update.py --stack api-agents        # redeploy live YAML + Env as-is, repull

Prints only stack id, env KEY names, which ${VARS} the YAML needs, sha256 of the YAML
and the HTTP status. Never prints env values or YAML content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9000"
TOKEN_PATH = pathlib.Path("/root/.portainer-token")
ENDPOINT_ID = 1
OPTIONAL_EMPTY = {"SENTRY_DSN"}


def req(method: str, path: str, body: dict | None = None, query: dict | None = None):
    url = BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = None if body is None else json.dumps(body).encode()
    headers = {"X-API-Key": TOKEN_PATH.read_text().strip(), "Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=300) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode("utf-8", "replace")[:600]}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", required=True)
    ap.add_argument("--yaml", type=pathlib.Path)
    ap.add_argument("--set-env", action="append", default=[])
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    code, stacks = req("GET", "/api/stacks")
    matches = [s for s in stacks if s.get("Name") == args.stack]
    if code != 200 or len(matches) != 1:
        print(f"stack {args.stack!r} not found (http={code})")
        return 2
    stack = matches[0]
    stack_id = stack["Id"]
    env = [dict(e) for e in (stack.get("Env") or [])]

    code, file_data = req("GET", f"/api/stacks/{stack_id}/file")
    if code != 200:
        print(f"file http={code}")
        return 2
    live_yaml = file_data.get("StackFileContent", "")
    yaml_text = args.yaml.read_text(encoding="utf-8") if args.yaml else live_yaml

    for item in args.set_env:
        key, _, value = item.partition("=")
        for e in env:
            if e["name"] == key:
                e["value"] = value
                break
        else:
            env.append({"name": key, "value": value})

    # Compose interpolates values, not comments: skip comment lines.
    body_lines = [ln for ln in yaml_text.splitlines() if not ln.lstrip().startswith("#")]
    needed = sorted(set(re.findall(r"\$\{([A-Z0-9_]+)", "\n".join(body_lines))))
    present = {e["name"]: bool(e.get("value")) for e in env}
    missing = [k for k in needed if not present.get(k) and k not in OPTIONAL_EMPTY]
    print(f"stack={args.stack} id={stack_id} env_keys={sorted(present)}")
    print(f"yaml live={sha(live_yaml)} new={sha(yaml_text)} needs={needed}")
    if missing:
        print(f"ABORT: YAML needs env without value: {missing}")
        return 3
    if args.dry_run:
        print("dry-run: nothing sent")
        return 0

    body = {
        "StackFileContent": yaml_text,
        "Env": env,
        "Prune": bool(args.prune),
        "PullImage": True,
        "RepullImageAndRedeploy": True,
    }
    code, data = req("PUT", f"/api/stacks/{stack_id}", body=body, query={"endpointId": ENDPOINT_ID})
    print(f"update http={code} prune={bool(args.prune)}")
    if code not in (200, 201):
        print(f"error: {data.get('error', '')[:400]}")
        return 1

    code, after = req("GET", f"/api/stacks/{stack_id}")
    keys_after = sorted(e["name"] for e in (after.get("Env") or []))
    print(f"env_keys_after={keys_after}")
    if keys_after != sorted(present):
        print("WARNING: env keys changed after update")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
