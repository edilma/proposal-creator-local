"""Shared test helpers: drive the real HTTP handler with no socket."""

import io

from proposal_creator.web import Handler


class FakeSocket:
    """In-memory stand-in so handler tests need no real socket."""

    def __init__(self, request_bytes: bytes) -> None:
        self._reader = io.BytesIO(request_bytes)
        self.sent = io.BytesIO()

    def makefile(self, mode: str, *args: object, **kwargs: object) -> io.BytesIO:
        return self._reader if "r" in mode else self.sent

    def sendall(self, data: bytes) -> None:
        self.sent.write(data)


def handle_request(db_path: object, request: bytes) -> str:
    handler_cls = type("BoundHandler", (Handler,), {"db_path": db_path})
    fake = FakeSocket(request)
    handler_cls(fake, ("127.0.0.1", 0), None)
    return fake.sent.getvalue().decode("utf-8")
