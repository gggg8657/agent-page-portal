"""계정 저장소 고르기 — AUTH_BACKEND(config.py): local(기본, 임시) | external | <모듈>:<클래스>.
다른 모듈은 `from . import auth` 뒤 auth.backend() 로만 쓴다(처음 부를 때 한 번 만듦)."""
import importlib

from .. import config
from .base import AuthBackend, AuthError, Unsupported, User

__all__ = ["AuthBackend", "AuthError", "Unsupported", "User", "backend"]
_B = {}


def _make(spec):
    if spec == "local":
        from .local import LocalBackend
        return LocalBackend()
    if spec == "external":
        from .external import ExternalBackend
        return ExternalBackend()
    mod, _, cls = spec.partition(":")
    if not (mod and cls):
        raise SystemExit(f"AUTH_BACKEND={spec!r} — local | external | <모듈>:<클래스>")
    b = getattr(importlib.import_module(mod), cls)()
    if not isinstance(b, AuthBackend):
        raise SystemExit(f"AUTH_BACKEND={spec!r} 는 fastportal.auth.base.AuthBackend 를 이어야 합니다")
    return b


def backend():
    if "b" not in _B:
        _B["b"] = _make(config.AUTH_BACKEND)
    return _B["b"]


def is_local():
    return backend().name == "local"
