from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uv", required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    arguments = parser.parse_args()

    initialize_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "release-gate", "version": "1.0"},
        },
    }
    requests = [
        initialize_request,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    command = [
        arguments.uv,
        "run",
        "--frozen",
        "--no-dev",
        "--project",
        str(arguments.project),
        str(arguments.project / "server.py"),
    ]
    started = time.perf_counter()
    process = subprocess.run(
        command,
        input=(
            "\n".join(json.dumps(item, separators=(",", ":")) for item in requests)
            + "\n"
        ).encode("utf-8"),
        capture_output=True,
        timeout=arguments.timeout,
        check=False,
    )
    elapsed = time.perf_counter() - started
    stdout = process.stdout.decode("utf-8", errors="replace")
    stderr = process.stderr.decode("utf-8", errors="replace")
    responses: dict[int, object] = {}
    for line in stdout.splitlines():
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        response_id = candidate.get("id")
        if isinstance(response_id, int):
            responses[response_id] = candidate

    print(
        json.dumps(
            {
                "exit_code": process.returncode,
                "seconds": round(elapsed, 3),
                "initialize_response": responses.get(1),
                "tools_response": responses.get(2),
                "stdout": stdout,
                "stderr": stderr,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
