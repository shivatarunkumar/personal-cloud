-- Shared helpers used by later migrations.

-- Keeps updated_at current on every UPDATE (attached per table with a trigger).
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;
