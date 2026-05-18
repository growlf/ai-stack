"""Shepherd control-plane entry point.

Run as: python -m shepherd_control
Or via the Dockerfile CMD override.
"""

import os

import uvicorn


def main():
    port = int(os.environ.get("SHEPHERD_CONTROL_PORT", "40117"))
    host = os.environ.get("SHEPHERD_CONTROL_HOST", "0.0.0.0")
    uvicorn.run(
        "shepherd_control.app:app",
        host=host,
        port=port,
        log_level=os.environ.get("SHEPHERD_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
