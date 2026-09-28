-- Objects are now stored under readable paths instead of ids:
--   {GCS_PREFIX}{username}/{folder path}/{file name}         live files
--   {GCS_PREFIX}{username}/{folder path}/                    a folder (an empty marker object)
--   {GCS_PREFIX}_trash/{username}/{deleted at}/{path}        anything in the trash
-- files.gcs_key follows the file around; folders get a key for their marker object.
-- Existing objects are moved by `make relayout` (the app also does it on the next change).

ALTER TABLE folders ADD COLUMN gcs_key text;
CREATE UNIQUE INDEX folders_gcs_key_key ON folders (gcs_key);
