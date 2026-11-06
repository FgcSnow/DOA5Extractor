from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from pathlib import Path

EXPECTED_SHA256 = "2771e0d2707c163d385e37afe65939e2fe5ec151f26acce9e080eb2f5d1baff6"
KEY_SIZE = 522
_key = ContextVar("player_decoding_key", default=None)

class KeyRequired(ValueError):
    pass

MISSING = "Key required: add your PC game folder (the one with game.exe) as a source, or give your own key file."

def read_key(path):
    if not path:
        raise KeyRequired(MISSING)
    with Path(path).open("rb") as stream:
        value = stream.read(65537)
    if not value or len(value) > 65536 or hashlib.sha256(value).hexdigest() != EXPECTED_SHA256:
        raise KeyRequired("Key required: wrong key file (SHA-256).")
    return value

def find_key_in_game(roots):
    # 0.5.3 : la cle est une chaine fixe du game.exe PC du joueur ; on la trouve par son empreinte seulement
    for root in roots or ():
        exe = Path(root) / "game.exe"
        if not exe.is_file():
            continue
        for piece in exe.read_bytes().split(b"\0"):
            if len(piece) >= KEY_SIZE and hashlib.sha256(piece[-KEY_SIZE:]).hexdigest() == EXPECTED_SHA256:
                return piece[-KEY_SIZE:]
    return None

@contextmanager
def key_context(path=None, roots=()):
    value = read_key(path) if path else find_key_in_game(roots)
    token = _key.set(value)
    try:
        yield value
    finally:
        _key.reset(token)

def require_key():
    value = _key.get()
    if value is None:
        raise KeyRequired(MISSING)
    return value
