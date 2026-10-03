"""Bounded mTLS HTTP workers; an idle handshake never blocks accept()."""

from __future__ import annotations

from http.server import ThreadingHTTPServer
import socket
import ssl
import threading


class PrivateTLSServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, address, handler, *, tls_context: ssl.SSLContext,
                 request_timeout_seconds: float = 5.0, max_workers: int = 24):
        if not 0 < request_timeout_seconds <= 30 or not 1 <= max_workers <= 64:
            raise ValueError("private_tls_server_limits_invalid")
        self.tls_context = tls_context
        self.request_timeout_seconds = request_timeout_seconds
        self._slots = threading.BoundedSemaphore(max_workers)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        secured = None
        try:
            request.settimeout(self.request_timeout_seconds)
            # TLS on a listening socket performs the handshake in accept().
            # Wrap only the accepted socket, in its bounded worker instead.
            secured = self.tls_context.wrap_socket(request, server_side=True)
            self.finish_request(secured, client_address)
        except (OSError, ssl.SSLError):
            pass  # Timeout/disconnect is not a listener failure.
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(secured if secured is not None else request)
            self._slots.release()

    def handle_error(self, request, client_address):
        # Never emit request bodies, credentials, or exception text.
        return

    def shutdown_request(self, request):
        try:
            request.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        self.close_request(request)
