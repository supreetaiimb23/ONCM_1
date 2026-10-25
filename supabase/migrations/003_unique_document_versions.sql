-- Upgrade an EXISTING database - run once in the Supabase SQL Editor. New installs get this from supabase/schema.sql,
-- and the app also creates the index itself on start.
-- Before this, two uploads of a new version at the same moment could both become e.g. v2. Find any such pairs first:
--   select document_id, version, count(*) from public.document_versions group by 1, 2 having count(*) > 1;
-- and renumber them (the newer row gets the next free number) before creating the index.
create unique index if not exists uq_doc_version on public.document_versions (document_id, version);
