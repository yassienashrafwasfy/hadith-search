"""Run the app under a profiler for a fixed time, then shut it down cleanly so the profiler can write its report.

    python -m scalene run --cpu-only -o out.json scripts/serve_for_profiling.py --- 120

The argument after `---` is how many seconds the server stays up (default 120).
"""

import sys
import threading

import uvicorn


def main(seconds: float = 120, port: int = 8000) -> None:
    server = uvicorn.Server(
        uvicorn.Config("main:app", host="0.0.0.0", port=port, log_level="warning")
    )
    threading.Timer(seconds, lambda: setattr(server, "should_exit", True)).start()
    server.run()


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 120)
