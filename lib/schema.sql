-- 공고, 매칭, 지원현황, 문서 버전만 SQLite 가 관리한다.
-- 경험 DB(users/*/experiences/*.md)는 Markdown 으로 유지하며 여기로 옮기지 않는다.
-- 스키마 변경은 lib/db.py 의 MIGRATIONS 에 새 항목을 추가하는 방식으로 한다.
-- 이 파일은 버전 1 (초기 스키마) 이다.

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,              -- paste | worknet | saramin
    source_job_id   TEXT,                       -- 출처 쪽 공고 ID (붙여넣기는 NULL)
    company         TEXT NOT NULL,
    position        TEXT NOT NULL,
    deadline        TEXT,                       -- YYYY-MM-DD, 상시채용은 NULL
    employment_type TEXT,
    required_json   TEXT NOT NULL DEFAULT '[]', -- 필수조건 배열
    preferred_json  TEXT NOT NULL DEFAULT '[]', -- 우대조건 배열
    url             TEXT,
    raw_path        TEXT,                       -- jobs/*.md 원문 캐시 경로
    content_hash    TEXT,                       -- 중복 판정용
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    closed          INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1))
);
-- 같은 출처의 같은 공고는 한 번만. source_job_id 가 NULL 인 행은 제약에서 제외된다.
CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_source ON jobs (source, source_job_id);
CREATE INDEX IF NOT EXISTS ix_jobs_content_hash ON jobs (content_hash);
CREATE INDEX IF NOT EXISTS ix_jobs_deadline ON jobs (closed, deadline);

CREATE TABLE IF NOT EXISTS matches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user          TEXT NOT NULL,
    job_id        INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    score         REAL NOT NULL,                -- 0~100
    required_hit  REAL,                         -- 필수 충족률 0~1
    preferred_hit REAL,                         -- 우대 충족률 0~1
    evidence_json TEXT NOT NULL DEFAULT '[]',   -- 조건별 판정과 근거 경험 파일명
    missing_json  TEXT NOT NULL DEFAULT '[]',   -- 부족한 조건
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS ix_matches_user_score ON matches (user, score DESC);
CREATE INDEX IF NOT EXISTS ix_matches_job ON matches (job_id);

CREATE TABLE IF NOT EXISTS applications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user       TEXT NOT NULL,
    job_id     INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    status     TEXT NOT NULL DEFAULT '관심',    -- 관심 | 준비중 | 지원완료 | 합격 | 불합격 | 대기
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (user, job_id)
);

CREATE TABLE IF NOT EXISTS application_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications (id) ON DELETE CASCADE,
    from_status    TEXT,                        -- 최초 등록은 NULL
    to_status      TEXT NOT NULL,
    note           TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS ix_events_application ON application_events (application_id, created_at);

CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user       TEXT NOT NULL,
    job_id     INTEGER REFERENCES jobs (id) ON DELETE SET NULL, -- 공고 없이 쓴 문서는 NULL
    kind       TEXT NOT NULL CHECK (kind IN ('coverletter', 'resume', 'portfolio')),
    version    INTEGER NOT NULL DEFAULT 1,
    path       TEXT NOT NULL,                   -- outputs/ 아래 상대경로
    char_count INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS ix_documents_user_job ON documents (user, job_id, kind, version);
