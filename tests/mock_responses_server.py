#!/usr/bin/env python3
"""Tiny local Responses API used to prove the real Codex hook lifecycle."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def response_stream(response_id: str) -> bytes:
    events: list[dict[str, Any]] = [
        {"type": "response.created", "response": {"id": response_id}},
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "role": "assistant",
                "id": response_id,
                "content": [{"type": "output_text", "text": "done"}],
            },
        },
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "usage": {
                    "input_tokens": 0,
                    "input_tokens_details": None,
                    "output_tokens": 0,
                    "output_tokens_details": None,
                    "total_tokens": 0,
                },
            },
        },
    ]
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
        for event in events
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-file", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()
    capture_lock = threading.Lock()
    request_number = 0

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - standard-library callback
            nonlocal request_number
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            try:
                payload: Any = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {"invalid_json": True}
            with capture_lock:
                args.capture_file.parent.mkdir(parents=True, exist_ok=True)
                with args.capture_file.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
                request_number += 1
                response_id = f"route09_{request_number}"
            body = response_stream(response_id)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - standard-library callback
            body = b'{"object":"list","data":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    args.port_file.parent.mkdir(parents=True, exist_ok=True)
    args.port_file.write_text(str(server.server_port), encoding="utf-8")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
