import socket
import socketserver
from pathlib import Path
from typing import Callable, Optional

class _DaemonRequestHandler(socketserver.BaseRequestHandler):
    """Handles incoming client requests on the UNIX domain stream socket."""

    server: '_DaemonSocketServer'

    def handle(self) -> None:
        try:
            self.request.settimeout(5.0)
        except OSError:
            pass

        chunks: list[bytes] = []
        aborted = False
        while True:
            try:
                data = self.request.recv(4096)
                if not data:
                    break
                chunks.append(data)
            except (ConnectionResetError, BrokenPipeError, OSError, socket.timeout):
                aborted = True
                break

        if aborted or not chunks:
            return

        try:
            text = b"".join(chunks).decode("utf-8", errors="replace")
        except Exception:
            return

        cleaned = text.strip()
        if not cleaned:
            return

        on_text: Optional[Callable[[str], None]] = getattr(self.server, "on_text", None)
        if on_text is not None:
            on_text(cleaned)

        try:
            self.request.sendall(b"OK\n")
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass

class _DaemonSocketServer(socketserver.ThreadingUnixStreamServer):
    """Multi-threaded UNIX domain stream socket server for daemon IPC."""
    address_family = socket.AF_UNIX
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(
        self,
        server_address: str | Path,
        on_text: Callable[[str], None],
    ) -> None:
        self.on_text = on_text
        super().__init__(str(server_address), _DaemonRequestHandler)

    def server_bind(self) -> None:
        try:
            path = Path(self.server_address)
            if path.exists() or path.is_symlink():
                path.unlink(missing_ok=True)
        except OSError:
            pass
        super().server_bind()
