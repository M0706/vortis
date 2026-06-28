from sync_tcp import run_sync_tcp_server
from async_tcp import AsyncTCPServer

if __name__ == "__main__":  # pragma: no cover - process entrypoint
    # run_sync_tcp_server()
    AsyncTCPServer().run()
