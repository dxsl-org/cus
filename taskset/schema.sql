-- Task item store (phase 8). Invariants live here, not only in Python, so a raw
-- UPDATE from a notebook or the Studio cannot bypass review:
--   * item_versions, reviews, verifier_runs, edits are append-only;
--   * kind, source_id and split of an item never change (moving items between
--     splits would leak test data into train);
--   * status moves only along status_transitions;
--   * approved/corrected needs a review of the *current* version with that verdict;
--   * auto (and any acceptance of rl_verifiable) needs verifier evidence on the current version.

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sources (
    id          TEXT PRIMARY KEY,
    license     TEXT NOT NULL,
    usage_scope TEXT NOT NULL CHECK (usage_scope IN ('train_ok', 'research_only', 'blocked')),
    note        TEXT
);

CREATE TABLE IF NOT EXISTS items (
    id                 TEXT PRIMARY KEY,
    kind               TEXT NOT NULL CHECK (kind IN ('sft', 'multi_turn', 'preference', 'reasoning', 'rl_verifiable',
                                                     'eval_mcq', 'eval_open', 'eval_code')),
    source_id          TEXT NOT NULL REFERENCES sources (id),
    split              TEXT NOT NULL CHECK (split IN ('train', 'dev', 'test', 'holdout')),
    status             TEXT NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft', 'pending', 'auto', 'approved', 'corrected', 'dropped')),
    current_version_id INTEGER REFERENCES item_versions (id),
    flags              TEXT NOT NULL DEFAULT '[]',  -- JSON list from FLAGS; any flag blocks export
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    CHECK (kind NOT LIKE 'eval_%' OR split IN ('test', 'holdout'))
);

CREATE TABLE IF NOT EXISTS item_versions (
    id               INTEGER PRIMARY KEY,
    item_id          TEXT NOT NULL REFERENCES items (id),
    version_no       INTEGER NOT NULL,
    parent_id        INTEGER REFERENCES item_versions (id),
    content_json     TEXT NOT NULL,
    content_sha256   TEXT NOT NULL,
    taxonomy_version INTEGER NOT NULL,
    origin           TEXT NOT NULL CHECK (origin GLOB 'human:?*' OR origin GLOB 'model:?*' OR origin GLOB 'import:?*'),
    created_by       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (item_id, version_no)
);

CREATE TABLE IF NOT EXISTS reviews (
    id         INTEGER PRIMARY KEY,
    item_id    TEXT NOT NULL REFERENCES items (id),
    version_id INTEGER NOT NULL REFERENCES item_versions (id),
    reviewer   TEXT NOT NULL CHECK (reviewer <> ''),
    verdict    TEXT NOT NULL CHECK (verdict IN ('approved', 'corrected', 'dropped', 'needs_work')),
    comment    TEXT,
    at         TEXT NOT NULL
);

-- role: reference = the item's own answer must pass; negative = a known-wrong answer must fail.
CREATE TABLE IF NOT EXISTS verifier_runs (
    id               INTEGER PRIMARY KEY,
    item_id          TEXT NOT NULL REFERENCES items (id),
    version_id       INTEGER NOT NULL REFERENCES item_versions (id),
    role             TEXT NOT NULL CHECK (role IN ('reference', 'negative', 'candidate')),
    candidate_sha256 TEXT NOT NULL,
    passed           INTEGER NOT NULL CHECK (passed IN (0, 1)),
    detail           TEXT,
    at               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edits (
    id        INTEGER PRIMARY KEY,
    item_id   TEXT NOT NULL REFERENCES items (id),
    field     TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    by        TEXT NOT NULL,
    at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS status_transitions (
    from_status TEXT NOT NULL,
    to_status   TEXT NOT NULL,
    PRIMARY KEY (from_status, to_status)
);
INSERT OR IGNORE INTO status_transitions VALUES
    ('draft', 'pending'), ('draft', 'dropped'),
    ('pending', 'draft'), ('pending', 'auto'), ('pending', 'approved'), ('pending', 'corrected'), ('pending', 'dropped'),
    ('auto', 'pending'), ('auto', 'approved'), ('auto', 'corrected'), ('auto', 'dropped'),
    ('approved', 'pending'), ('approved', 'corrected'), ('approved', 'dropped'),
    ('corrected', 'pending'), ('corrected', 'approved'), ('corrected', 'dropped'),
    ('dropped', 'draft');

CREATE INDEX IF NOT EXISTS items_split_status ON items (split, status);
CREATE INDEX IF NOT EXISTS reviews_version ON reviews (version_id);
CREATE INDEX IF NOT EXISTS runs_version ON verifier_runs (version_id, role);

-- Append-only tables.
CREATE TRIGGER IF NOT EXISTS item_versions_no_update BEFORE UPDATE ON item_versions
BEGIN SELECT RAISE(ABORT, 'item_versions are immutable; create a new version'); END;
CREATE TRIGGER IF NOT EXISTS item_versions_no_delete BEFORE DELETE ON item_versions
BEGIN SELECT RAISE(ABORT, 'item_versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS reviews_no_update BEFORE UPDATE ON reviews
BEGIN SELECT RAISE(ABORT, 'reviews are append-only'); END;
CREATE TRIGGER IF NOT EXISTS reviews_no_delete BEFORE DELETE ON reviews
BEGIN SELECT RAISE(ABORT, 'reviews are append-only'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_update BEFORE UPDATE ON verifier_runs
BEGIN SELECT RAISE(ABORT, 'verifier_runs are append-only'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_delete BEFORE DELETE ON verifier_runs
BEGIN SELECT RAISE(ABORT, 'verifier_runs are append-only'); END;
CREATE TRIGGER IF NOT EXISTS edits_no_update BEFORE UPDATE ON edits
BEGIN SELECT RAISE(ABORT, 'edits are append-only'); END;
CREATE TRIGGER IF NOT EXISTS edits_no_delete BEFORE DELETE ON edits
BEGIN SELECT RAISE(ABORT, 'edits are append-only'); END;
CREATE TRIGGER IF NOT EXISTS items_no_delete BEFORE DELETE ON items
BEGIN SELECT RAISE(ABORT, 'items are never deleted; set status dropped'); END;

CREATE TRIGGER IF NOT EXISTS items_insert_draft BEFORE INSERT ON items WHEN NEW.status <> 'draft'
BEGIN SELECT RAISE(ABORT, 'items start as draft'); END;

CREATE TRIGGER IF NOT EXISTS items_fixed_identity BEFORE UPDATE OF kind, source_id, split ON items
WHEN NEW.kind <> OLD.kind OR NEW.source_id <> OLD.source_id OR NEW.split <> OLD.split
BEGIN SELECT RAISE(ABORT, 'kind, source_id and split of an item are fixed'); END;

CREATE TRIGGER IF NOT EXISTS items_version_belongs BEFORE UPDATE OF current_version_id ON items
WHEN NEW.current_version_id IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM item_versions v WHERE v.id = NEW.current_version_id AND v.item_id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'current_version_id belongs to another item'); END;

CREATE TRIGGER IF NOT EXISTS items_status_transition BEFORE UPDATE OF status ON items
WHEN NEW.status <> OLD.status
 AND NOT EXISTS (SELECT 1 FROM status_transitions t WHERE t.from_status = OLD.status AND t.to_status = NEW.status)
BEGIN SELECT RAISE(ABORT, 'illegal status transition'); END;

CREATE TRIGGER IF NOT EXISTS items_pending_needs_version BEFORE UPDATE OF status ON items
WHEN NEW.status <> 'draft' AND NEW.status <> 'dropped' AND NEW.current_version_id IS NULL
BEGIN SELECT RAISE(ABORT, 'item has no content version'); END;

CREATE TRIGGER IF NOT EXISTS items_accept_needs_review BEFORE UPDATE OF status, current_version_id ON items
WHEN NEW.status IN ('approved', 'corrected')
 AND NOT EXISTS (SELECT 1 FROM reviews r
                 WHERE r.item_id = NEW.id AND r.version_id = NEW.current_version_id AND r.verdict = NEW.status)
BEGIN SELECT RAISE(ABORT, 'approved/corrected needs a review of the current version with that verdict'); END;

CREATE TRIGGER IF NOT EXISTS items_auto_needs_verifier BEFORE UPDATE OF status, current_version_id ON items
WHEN NEW.status = 'auto'
 AND NOT EXISTS (SELECT 1 FROM verifier_runs v
                 WHERE v.version_id = NEW.current_version_id AND v.role = 'reference' AND v.passed = 1)
BEGIN SELECT RAISE(ABORT, 'auto needs a passing reference verifier run on the current version'); END;

-- Without a failing known-wrong answer the verifier may accept anything, and RL learns to game it.
CREATE TRIGGER IF NOT EXISTS items_rl_needs_negative BEFORE UPDATE OF status, current_version_id ON items
WHEN NEW.kind = 'rl_verifiable' AND NEW.status IN ('auto', 'approved', 'corrected')
 AND (NOT EXISTS (SELECT 1 FROM verifier_runs v
                  WHERE v.version_id = NEW.current_version_id AND v.role = 'reference' AND v.passed = 1)
   OR NOT EXISTS (SELECT 1 FROM verifier_runs v
                  WHERE v.version_id = NEW.current_version_id AND v.role = 'negative' AND v.passed = 0))
BEGIN SELECT RAISE(ABORT, 'rl_verifiable needs a passing reference and a failing negative run on the current version'); END;
