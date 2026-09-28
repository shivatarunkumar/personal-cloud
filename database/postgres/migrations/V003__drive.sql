-- Each user's drive: folders, files and an audit trail.
-- Only metadata lives here. A file's bytes are one GCS object at
--   {GCS_PREFIX}u/{user_id}/f/{file_id}
-- and that key never changes: renaming or moving anything is a database update only.

CREATE TABLE folders (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- NULL = the user's root
    parent_id    uuid REFERENCES folders (id) ON DELETE CASCADE,
    name         text NOT NULL CHECK (
                     length(btrim(name)) BETWEEN 1 AND 255
                     AND position('/' IN name) = 0
                     AND name NOT IN ('.', '..')),
    -- full path under the root, e.g. "Trips/2026"; rewritten for a subtree on rename/move
    path         text NOT NULL,
    -- in the trash. Trashing a folder stamps it and everything under it with the same
    -- time; trash_root marks the item the person actually deleted (what Trash lists).
    trashed_at   timestamptz,
    trash_root   boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- one live folder of a name per parent, case-insensitively (like Finder and OneDrive)
CREATE UNIQUE INDEX folders_sibling_name_key
    ON folders (user_id, coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name))
    WHERE trashed_at IS NULL;
CREATE INDEX folders_parent_idx ON folders (user_id, parent_id) WHERE trashed_at IS NULL;
CREATE INDEX folders_path_idx ON folders (user_id, path text_pattern_ops);
CREATE INDEX folders_trash_idx ON folders (user_id, trashed_at) WHERE trash_root;

CREATE TRIGGER folders_set_updated_at BEFORE UPDATE ON folders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE files (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- NULL = the root. Deleting a folder for good deletes its files' rows too; the
    -- service deletes their objects from the bucket first.
    folder_id           uuid REFERENCES folders (id) ON DELETE CASCADE,
    name                text NOT NULL CHECK (
                            length(btrim(name)) BETWEEN 1 AND 255
                            AND position('/' IN name) = 0
                            AND name NOT IN ('.', '..')),
    gcs_key             text NOT NULL UNIQUE,
    content_type        text NOT NULL DEFAULT 'application/octet-stream',
    kind                text NOT NULL DEFAULT 'other'
                        CHECK (kind IN ('image', 'video', 'audio', 'pdf', 'text', 'archive', 'document', 'other')),
    size_bytes          bigint NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    status              text NOT NULL DEFAULT 'uploading' CHECK (status IN ('uploading', 'ready')),
    -- the resumable session while uploading, so a reloaded page can carry on
    upload_session_url  text,
    upload_expires_at   timestamptz,
    trashed_at          timestamptz,
    trash_root          boolean NOT NULL DEFAULT false,
    last_opened_at      timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- one live file of a name per folder; an upload in progress holds its name too
CREATE UNIQUE INDEX files_sibling_name_key
    ON files (user_id, coalesce(folder_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name))
    WHERE trashed_at IS NULL;
CREATE INDEX files_folder_idx ON files (user_id, folder_id) WHERE trashed_at IS NULL AND status = 'ready';
CREATE INDEX files_recent_idx ON files (user_id, updated_at DESC) WHERE trashed_at IS NULL AND status = 'ready';
CREATE INDEX files_kind_idx ON files (user_id, kind, created_at DESC) WHERE trashed_at IS NULL AND status = 'ready';
CREATE INDEX files_trash_idx ON files (user_id, trashed_at) WHERE trash_root;
CREATE INDEX files_uploading_idx ON files (upload_expires_at) WHERE status = 'uploading';
CREATE INDEX files_name_trgm_idx ON files USING gin (name gin_trgm_ops);

CREATE TRIGGER files_set_updated_at BEFORE UPDATE ON files
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Append-only history of what happened to whose files.
CREATE TABLE file_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- no foreign keys: the history outlives the file
    file_id     uuid,
    folder_id   uuid,
    action      text NOT NULL CHECK (action IN (
                    'upload', 'create_folder', 'rename', 'move', 'copy',
                    'trash', 'restore', 'delete', 'download')),
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX file_events_user_idx ON file_events (user_id, created_at DESC);
