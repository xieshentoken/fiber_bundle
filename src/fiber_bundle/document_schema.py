"""Additive PDF task binding; existing source/run/receipt semantics remain intact."""

SCHEMA_V8 = """
CREATE TABLE document_tasks (
 run_id TEXT PRIMARY KEY REFERENCES execution_runs(id),
 receipt_id TEXT NOT NULL REFERENCES receipts(job_id),
 asset_id TEXT NOT NULL REFERENCES assets(id),
 source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64),
 source_bytes INTEGER NOT NULL CHECK(source_bytes>0 AND source_bytes<=8388608),
 worker_image TEXT NOT NULL, worker_timeout INTEGER NOT NULL CHECK(worker_timeout>0),
 capture_task_id TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('queued','extracting','extracted','failed','interrupted')),
 result_sha256 TEXT,
 page_count INTEGER CHECK(page_count>0 AND page_count<=100),
 ocr_page_count INTEGER CHECK(ocr_page_count>=0 AND ocr_page_count<=100),
 review_page_count INTEGER CHECK(review_page_count>=0 AND review_page_count<=100),
 error_code TEXT,
 CHECK(state!='extracted' OR (result_sha256 IS NOT NULL AND page_count IS NOT NULL
 AND ocr_page_count IS NOT NULL AND review_page_count IS NOT NULL))
) STRICT;
"""
