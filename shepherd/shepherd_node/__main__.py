"""Entry point for the shepherd-node sidecar.

Run as: python -m shepherd_node
Or in Docker via the CMD in the Dockerfile.
"""

import os
import uvicorn


def main():
    port = int(os.environ.get("SHEPHERD_NODE_PORT", "40116"))
    host = os.environ.get("SHEPHERD_NODE_HOST", "0.0.0.0")
    uvicorn.run(
        "shepherd_node.app:app",
        host=host,
        port=port,
        log_level=os.environ.get("SHEPHERD_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
