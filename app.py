"""Application entrypoint for the dialog generation system."""
import os
import sys


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


BASE_DIR = os.path.dirname(__file__)
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from dialog_system.api import app  # noqa: E402


if __name__ == "__main__":
    import uvicorn  # noqa: E402

    port = int(os.getenv("PORT", 8000))
    print(f"[INFO] Starting server on http://0.0.0.0:{port}")
    print(f"[INFO] Open browser: http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
