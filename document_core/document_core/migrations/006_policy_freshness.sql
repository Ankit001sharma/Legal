-- Phase 28: policy freshness timestamp for optional stale filtering.
ALTER TABLE policy_documents
  ADD COLUMN IF NOT EXISTS last_verified_at timestamptz DEFAULT now();

UPDATE policy_documents
SET last_verified_at = COALESCE(indexed_at, now())
WHERE last_verified_at IS NULL;
