"""Sign-up, sign-in, refresh-token rotation, passwords and devices."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import Settings
from app.models.user import RefreshToken, User
from app.schemas.auth import RegisterIn
from app.services.errors import ServiceError

log = logging.getLogger("cloud.auth")

MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


# ------------------------------------------------------------------ sign-up
async def register(session: AsyncSession, settings: Settings, data: RegisterIn) -> User:
    if await session.scalar(select(User.id).where(User.email == data.email)):
        raise ServiceError("An account with this email already exists", status_code=409, field="email")
    if await session.scalar(select(User.id).where(User.username == data.username)):
        raise ServiceError("That username is taken", status_code=409, field="username")

    now = datetime.now(UTC)
    user = User(
        email=data.email,
        username=data.username,
        display_name=data.display_name,
        password_hash=security.hash_password(data.password),
        password_changed_at=now,
        must_change_password=False,
        quota_bytes=settings.default_quota_bytes,
        used_bytes=0,
        failed_login_count=0,
    )
    session.add(user)
    await session.flush()
    log.info("new account: %s", user.username)
    return user


# ------------------------------------------------------------------ sign-in
async def authenticate(session: AsyncSession, login: str, password: str) -> User:
    login = login.strip().lower()
    user = await session.scalar(select(User).where(or_(User.email == login, User.username == login)))
    now = datetime.now(UTC)

    # Same message whichever check fails, so the response can't be used to discover
    # which addresses have accounts.
    invalid = ServiceError("Incorrect email/username or password", status_code=401)

    if user is None:
        security.hash_password(password)  # keep the timing similar to a real check
        log.info("sign-in failed: no account for that login (the reply does not say so)")
        raise invalid
    if user.locked_until and user.locked_until > now:
        minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        log.info("sign-in refused: %s is locked for another %d minute(s)", user.username, minutes)
        raise ServiceError(f"Too many failed attempts. Try again in {minutes} minute(s).", status_code=429)

    if not security.verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_login_count = 0
            log.warning(
                "%s locked for %d minutes after %d failed attempts",
                user.username,
                LOCKOUT_MINUTES,
                MAX_FAILED_LOGINS,
            )
        else:
            log.info(
                "sign-in failed: wrong password for %s (attempt %d of %d)",
                user.username,
                user.failed_login_count,
                MAX_FAILED_LOGINS,
            )
        raise invalid

    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    log.info("signed in: %s", user.username)
    return user


# ------------------------------------------------------------------ refresh tokens
async def issue_refresh_token(
    session: AsyncSession, settings: Settings, user: User, *, user_agent: str | None, ip: str | None
) -> tuple[str, uuid.UUID]:
    """(token for the cookie, its row id = this device's session id)."""
    token, token_hash = security.new_refresh_token()
    now = datetime.now(UTC)
    row = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=token_hash,
        expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
        user_agent=(user_agent or "")[:500] or None,
        ip=ip,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    return token, row.id


async def rotate_refresh_token(
    session: AsyncSession, settings: Settings, token: str, *, user_agent: str | None, ip: str | None
) -> tuple[User, str, uuid.UUID]:
    """Swap a refresh token for a new one. Reusing a revoked token signs the user out
    everywhere, because it means someone else has a copy."""
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == security.hash_refresh_token(token))
    )
    expired = ServiceError("Your session has expired. Please sign in again.", status_code=401)
    if row is None:
        raise expired

    now = datetime.now(UTC)
    if row.revoked_at is not None:
        if row.revoked_reason == "rotated":
            log.warning("refresh token reuse detected for user %s: signing out every device", row.user_id)
            await revoke_all(session, row.user_id, reason="reuse_detected")
        raise expired
    if row.expires_at <= now:
        raise expired

    user = await session.get(User, row.user_id)
    if user is None:
        raise expired

    new_token, new_hash = security.new_refresh_token()
    replacement = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=new_hash,
        expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
        user_agent=(user_agent or "")[:500] or None,
        ip=ip,
        created_at=now,
    )
    session.add(replacement)
    await session.flush()
    row.revoked_at = now
    row.revoked_reason = "rotated"
    row.replaced_by = replacement.id
    row.last_used_at = now
    return user, new_token, replacement.id


async def revoke_refresh_token(session: AsyncSession, token: str, reason: str = "logout") -> None:
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == security.hash_refresh_token(token))
    )
    if row and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        row.revoked_reason = reason


async def revoke_all(
    session: AsyncSession, user_id: uuid.UUID, reason: str, *, keep: uuid.UUID | None = None
) -> None:
    query = (
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )
    if keep:
        query = query.where(RefreshToken.id != keep)
    await session.execute(query)


# ------------------------------------------------------------------ account
async def change_password(
    session: AsyncSession, user: User, current: str, new: str, *, keep: uuid.UUID | None
) -> None:
    if not security.verify_password(current, user.password_hash):
        raise ServiceError("Your current password is not right", status_code=400, field="current_password")
    if current == new:
        raise ServiceError("Choose a password you haven't used here", field="new_password")
    user.password_hash = security.hash_password(new)
    user.password_changed_at = datetime.now(UTC)
    user.must_change_password = False
    # every other device signs in again; this one stays signed in
    await revoke_all(session, user.id, reason="password_reset", keep=keep)
    log.info("%s changed their password; other devices signed out", user.username)


async def sessions(session: AsyncSession, user: User) -> list[RefreshToken]:
    rows = await session.scalars(
        select(RefreshToken)
        .where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > datetime.now(UTC),
        )
        .order_by(RefreshToken.created_at.desc())
    )
    return list(rows)


async def sign_out_device(session: AsyncSession, user: User, token_id: uuid.UUID) -> None:
    row = await session.get(RefreshToken, token_id)
    if row is None or row.user_id != user.id or row.revoked_at is not None:
        raise ServiceError("That device is already signed out", status_code=404)
    row.revoked_at = datetime.now(UTC)
    row.revoked_reason = "signed_out"
