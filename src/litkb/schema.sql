CREATE TABLE IF NOT EXISTS papers (
  id TEXT PRIMARY KEY,                          -- OpenAlex W-id, else 'h' + sha1(doi|title)[:10]
  doi TEXT UNIQUE, openalex_id TEXT, s2_id TEXT, arxiv_id TEXT, pmid TEXT,
  title TEXT NOT NULL, abstract TEXT, year INTEGER, venue TEXT, authors_json TEXT,
  cited_by_count INTEGER, is_oa INTEGER, oa_url TEXT, landing_url TEXT,
  referenced_works_json TEXT,                   -- OpenAlex W-ids this paper cites (all, not only corpus)
  included INTEGER NOT NULL DEFAULT 1,
  pdf_status TEXT NOT NULL DEFAULT 'pending',   -- pending|ok|not_found|paywalled|failed
  pdf_source TEXT, pdf_sha1 TEXT, pdf_path TEXT,
  parse_status TEXT NOT NULL DEFAULT 'pending', -- pending|ok|failed|abstract_only
  index_status TEXT NOT NULL DEFAULT 'pending', -- pending|ok
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rqs (
  id TEXT PRIMARY KEY, text TEXT NOT NULL, sub_questions_json TEXT, keywords_json TEXT
);
CREATE TABLE IF NOT EXISTS paper_rq (
  paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE, rq_id TEXT REFERENCES rqs(id),
  undermind_rank INTEGER, undermind_score REAL,
  PRIMARY KEY (paper_id, rq_id)
);
CREATE TABLE IF NOT EXISTS citations (
  src_paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
  dst_paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
  PRIMARY KEY (src_paper_id, dst_paper_id)
);
CREATE TABLE IF NOT EXISTS retrieval_attempts (
  paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE, source TEXT, url TEXT, http_status INTEGER,
  outcome TEXT, ts TEXT DEFAULT CURRENT_TIMESTAMP
);
