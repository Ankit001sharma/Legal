ALTER TABLE policy_documents
  DROP CONSTRAINT IF EXISTS policy_documents_index_status_check;

ALTER TABLE policy_documents
  ADD CONSTRAINT policy_documents_index_status_check
  CHECK (index_status IN ('pending', 'indexed', 'failed', 'deleted'));
