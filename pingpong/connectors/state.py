"""Signed-JWT helpers for the OAuth state round-trip.

The state parameter is a short-lived JWT, signed with the same secret key
used for auth tokens (:mod:`pingpong.auth`), carrying the user_id, service
slug, tenant id and PKCE verifier so the callback can tie the redirect
back to the originating user without any server-side session storage.

We don't use :func:`pingpong.auth.encode_auth_token` directly because the
``AuthToken`` schema only carries ``sub``. This module encodes/decodes a
richer payload using the same signing secret.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

import jwt
from jwt.exceptions import PyJWTError

from pingpong.config import config
from pingpong.now import NowFn, utcnow

from .exceptions import OAuthStateError

DEFAULT_EXPIRY_SECONDS = 600


def encode_state(
    *,
    user_id: int,
    service: str,
    tenant: str | None,
    pkce_verifier: str | None,
    redirect_to: str | None = None,
    expiry: int = DEFAULT_EXPIRY_SECONDS,
    nowfn: NowFn = utcnow,
) -> str:
    now = nowfn()
    exp = now + timedelta(seconds=expiry)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "service": service,
        "tenant": tenant,
        "pkce_verifier": pkce_verifier,
    }
    if redirect_to is not None:
        payload["redirect_to"] = redirect_to
    secret = config.auth.secret_keys[0]
    return cast(
        str,
        jwt.encode(payload, secret.key, algorithm=secret.algorithm),
    )


def decode_state(token: str, nowfn: NowFn = utcnow) -> dict[str, Any]:
    exc: Exception | None = None
    for secret in config.auth.secret_keys:
        try:
            payload = jwt.decode(
                token,
                secret.key,
                algorithms=[secret.algorithm],
                options={"verify_exp": False, "verify_nbf": False},
            )
        except PyJWTError as e:
            exc = e
            continue

        now_ts = nowfn().timestamp()
        exp = payload.get("exp")
        if isinstance(exp, (int, float)) and now_ts > exp:
            raise OAuthStateError("OAuth state token expired")

        required = ("sub", "service")
        for key in required:
            if key not in payload:
                raise OAuthStateError(f"OAuth state token missing '{key}'")
        return payload

    raise OAuthStateError(
        f"OAuth state token signature invalid: {exc}" if exc else "OAuth state token invalid"
    )
