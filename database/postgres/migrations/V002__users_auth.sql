-- Accounts and sessions. Every account is an ordinary user with a private drive:
-- there are no roles and no admin.

CREATE TABLE users (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- stored lowercase so the unique index and lookups agree
    email                text NOT NULL CHECK (email = lower(email) AND email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    -- sign-in name, lowercase
    username             text NOT NULL CHECK (username ~ '^[a-z0-9][a-z0-9_.-]{1,49}$'),
    display_name         text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 100),
    -- argon2id hash; never a plaintext password
    password_hash        text NOT NULL,
    password_changed_at  timestamptz NOT NULL DEFAULT now(),
    -- set by `make reset-password`: the next sign-in must choose a new password
    must_change_password boolean NOT NULL DEFAULT false,
    -- NULL = unlimited
    quota_bytes          bigint CHECK (quota_bytes IS NULL OR quota_bytes >= 0),
    -- bytes of every ready file the user owns, trash included
    used_bytes           bigint NOT NULL DEFAULT 0 CHECK (used_bytes >= 0),
    -- brute-force protection for the login endpoint
    failed_login_count   integer NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
    locked_until         timestamptz,
    last_login_at        timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX users_email_key ON users (email);
CREATE UNIQUE INDEX users_username_key ON users (username);

CREATE TRIGGER users_set_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Refresh tokens are stored hashed and rotated on every use: using one revokes it and
-- points replaced_by at its successor, so a replayed token reveals a stolen session.
CREATE TABLE refresh_tokens (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash     text NOT NULL UNIQUE,
    expires_at     timestamptz NOT NULL,
    last_used_at   timestamptz,
    revoked_at     timestamptz,
    revoked_reason text CHECK (revoked_reason IN ('rotated', 'logout', 'reuse_detected', 'password_reset', 'signed_out')),
    replaced_by    uuid REFERENCES refresh_tokens (id) ON DELETE SET NULL,
    user_agent     text CHECK (length(user_agent) <= 500),
    ip             inet,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at)
);

-- "my signed-in devices" and "sign out everywhere"
CREATE INDEX refresh_tokens_active_idx ON refresh_tokens (user_id, expires_at DESC)
    WHERE revoked_at IS NULL;
CREATE INDEX refresh_tokens_expires_idx ON refresh_tokens (expires_at);
