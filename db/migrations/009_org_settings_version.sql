-- Version-stamp org rows so concurrent settings writers can be detected.
--
-- The org-wide close-all pauses copying transiently and restores it after
-- the flatten (copier CopierApp.close_all). The restore must NOT clobber a
-- write that happened in between -- e.g. the operator hitting STOP COPYING
-- mid-flatten. Settings are written from TWO processes (the api updates
-- orgs directly in SQL; the copier goes through Repo.set_org_setting), so
-- the guard lives here: a trigger bumps settings_version on EVERY update of
-- an org row, and the restore is an atomic compare-and-set on that version.
-- The bump is deliberately unconditional (not value-diffed): re-issuing
-- "copying_enabled = false" while it is already false is still an operator
-- intent the restore must honor.

ALTER TABLE orgs ADD COLUMN settings_version BIGINT NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION bump_org_settings_version() RETURNS trigger AS $$
BEGIN
    NEW.settings_version := OLD.settings_version + 1;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER orgs_settings_version
    BEFORE UPDATE ON orgs
    FOR EACH ROW EXECUTE FUNCTION bump_org_settings_version();
