"""Production/local launcher for CrisisCommand AI.

Uses Waitress and honors the hosting platform's PORT/HOST environment variables.
Works locally on Windows and on platforms such as Railway.
"""
import os
import traceback
from waitress import serve
from app import app


def start_server():
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    print(f"CrisisCommand AI running on {host}:{port}", flush=True)
    print("Using Waitress WSGI server.", flush=True)
    serve(
        app,
        host=host,
        port=port,
        threads=int(os.getenv("WEB_THREADS", "4")),
        channel_timeout=120,
        asyncore_use_poll=True,
    )


if __name__ == "__main__":
    try:
        start_server()
    except KeyboardInterrupt:
        print("\nCrisisCommand AI stopped by user.", flush=True)
    except Exception:
        print("\nCrisisCommand AI server error:", flush=True)
        traceback.print_exc()
        raise
