"""
claudefree — Anthropic-compatible gateway

Start with:
    claudefree

Or manually:
    uv run uvicorn server:app --host 0.0.0.0 --port 16324 --timeout-graceful-shutdown 5

Then in another terminal:
    claude

(After running setup-env.sh or setup-env.bat to set environment variables)
"""

from gateway.app import app, create_app

__all__ = ["app", "create_app"]

if __name__ == "__main__":
    import uvicorn

    from cli.process_registry import kill_all_best_effort
    from settings.env import get_settings

    cfg = get_settings()
    try:
        uvicorn.run(
            app,
            host=cfg.host,
            port=cfg.port,
            log_level="info",
            timeout_graceful_shutdown=5,
        )
    finally:
        kill_all_best_effort()
