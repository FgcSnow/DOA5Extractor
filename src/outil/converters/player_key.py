from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from pathlib import Path

EXPECTED_SHA256 = "2771e0d2707c163d385e37afe65939e2fe5ec151f26acce9e080eb2f5d1baff6"
_key = ContextVar("player_decoding_key", default=None)

class KeyRequired(ValueError):
    pass

def read_key(path):
    if not path:
        raise KeyRequired("Key required: give your own key file.")
    with Path(path).open("rb") as stream:
        value = stream.read(65537)
    if not value or len(value) > 65536 or hashlib.sha256(value).hexdigest() != EXPECTED_SHA256:
        raise KeyRequired("Key required: wrong key file (SHA-256).")
    return value

@contextmanager
def key_context(path=None):
    value = read_key(path) if path else None
    token = _key.set(value)
    try:
        yield
    finally:
        _key.reset(token)

def require_key():
    value = _key.get()
    if value is None:
        raise KeyRequired("Key required: give your own key file.")
    return value
