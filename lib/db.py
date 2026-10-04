"""SQLite 계층. 공고, 매칭, 지원현황, 문서 버전만 이 DB 가 관리한다.

경험 DB 는 Markdown 으로 유지하며 여기에 넣지 않는다 (ROADMAP 1-A).
"""
import contextlib
import os
import sqlite3
import sys
from pathlib import Path

from lib import config

SCHEMA_PATH = Path(__file__).resolve().parent / 'schema.sql'

TABLES = ('jobs', 'matches', 'applications', 'application_events', 'documents', 'runs')

# PRAGMA user_version 이 곧 스키마 버전이다. 버전 N 의 마이그레이션은 MIGRATIONS[N - 1].
# 버전 1 은 schema.sql 전체. 이후 변경은 SQL 문자열을 뒤에 추가한다 (기존 항목은 수정하지 않는다).
MIGRATIONS = [
    None,  # 1: schema.sql
    # 2: 실행 기록 (S11-17, 2차 설계검토 4장).
    #    "공통 실행기가 있다" 와 "실제 작성이 그 실행기를 거쳤다" 는 다른 말이다.
    #    퀴네앤드나겔 자소서가 엔진을 안 거치고 저장됐는데 2일 동안 아무도 몰랐다 —
    #    거쳤는지 되물을 자리가 없었기 때문이다. 그 자리를 만든다.
    """
    CREATE TABLE IF NOT EXISTS runs (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id         TEXT NOT NULL UNIQUE,
        user           TEXT NOT NULL,
        surface        TEXT NOT NULL,          -- web | cli | script. 어느 경로로 돌았나
        workflow       TEXT NOT NULL,          -- coverletter.generate | coverletter.revise
        company        TEXT,
        position       TEXT,
        question       TEXT,
        content_hash   TEXT,                   -- 검사한 본문의 sha256. 본문이 바뀌면 안 맞는다
        status         TEXT,                   -- ready_for_user | review_required | failed
        issues         TEXT,                   -- JSON 배열
        quality_check  INTEGER NOT NULL DEFAULT 0,  -- 의미 평가를 요청했나
        reviewed       INTEGER NOT NULL DEFAULT 0,  -- 실제로 평가 결과를 받았나
        inputs         TEXT,                   -- JSON. 사용한 경험 파일 등
        created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    );
    CREATE INDEX IF NOT EXISTS ix_runs_user ON runs (user, created_at);
    ALTER TABLE documents ADD COLUMN run_id TEXT;
    """,
]


# --- 테스트가 실제 DB 를 건드리지 못하게 막는 장치 (S11-17, 2차 설계검토 5-2) ---------
#
# 2026-10-02 에 tests/test_coverletter.py 가 db_path 를 빼먹어 실제 DB 에 테스트 행
# 3개가 들어갔다. 검토의 지적은 "다음부터 db_path 를 잊지 말자" 보다 강한 장치를 두라는
# 것이었다 — 미설정이면 실제 경로로 폴백하지 말고 실패해야 한다.
#
# 처음엔 tests/__init__.py 에서 환경변수를 켜려 했는데, `unittest discover tests` 는
# 그 파일을 import 하지 않는다(모듈을 최상위 이름으로 올린다). 그래서 476개 테스트가
# 도는 동안 장치가 꺼져 있었다. 활성화를 테스트 쪽 규율에 맡기지 않고 여기서 판단한다.

_ALLOW_DEFAULT = 'RESUMEPROJECT_ALLOW_DEFAULT_DB'


def _default_db_forbidden() -> bool:
    if os.environ.get(_ALLOW_DEFAULT):
        return False
    if os.environ.get('RESUMEPROJECT_REQUIRE_DB_PATH'):
        return True
    return 'unittest' in sys.modules or 'pytest' in sys.modules


@contextlib.contextmanager
def allow_default_db():
    """기본 DB 경로 연결을 잠시 허용한다.

    실제 DB 가 깨끗한지 확인하는 회귀 테스트처럼, **일부러** 기본 경로를 봐야 할 때만 쓴다.
    """
    prev = os.environ.get(_ALLOW_DEFAULT)
    os.environ[_ALLOW_DEFAULT] = '1'
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(_ALLOW_DEFAULT, None)
        else:
            os.environ[_ALLOW_DEFAULT] = prev


def get_conn(db_path=None):
    """외래키가 켜진 연결을 돌려준다. 행은 이름으로 접근할 수 있다 (row['company'])."""
    if db_path is None and _default_db_forbidden():
        raise RuntimeError(
            'db_path 없이 기본 DB 경로로 연결하려 했습니다. 테스트는 임시 DB 를 명시적으로 '
            '넘겨야 합니다. 일부러 기본 경로를 봐야 하면 lib.db.allow_default_db() 로 감싸세요.')
    path = Path(db_path) if db_path else config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db(db_path=None):
    """테이블을 만들고 미적용 마이그레이션을 적용한다. 여러 번 호출해도 안전하다.

    적용 후 스키마 버전을 돌려준다.
    """
    conn = get_conn(db_path)
    try:
        current = conn.execute('PRAGMA user_version').fetchone()[0]
        for version in range(current + 1, len(MIGRATIONS) + 1):
            sql = SCHEMA_PATH.read_text(encoding='utf-8') if version == 1 else MIGRATIONS[version - 1]
            # executescript 는 암묵적으로 COMMIT 하므로, 스크립트와 버전 기록을 한 트랜잭션으로 묶는다.
            conn.executescript(f'BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;')
        return conn.execute('PRAGMA user_version').fetchone()[0]
    finally:
        conn.close()


def delete_user_data(user, db_path=None):
    """사용자 홈 화면 삭제 기능(2026-09-27)에서 쓴다. matches/applications/documents 는
    job_id 로만 FK 가 걸려 있고 user 컬럼엔 외래키/CASCADE 가 없으므로, 파일(users/[이름],
    outputs/[이름])을 지우는 것과 별개로 DB 쪽도 여기서 직접 지워야 한다 — 안 그러면 같은
    이름으로 사용자를 다시 만들었을 때 예전 매칭/지원현황/문서 이력이 유령처럼 다시 보인다.
    application_events 는 applications 에 ON DELETE CASCADE 가 걸려 있어 따로 안 지워도 된다.
    jobs 는 여러 사용자가 같이 보는 공용 데이터라 여기서 건드리지 않는다.
    """
    conn = get_conn(db_path)
    try:
        conn.execute('DELETE FROM matches WHERE user = ?', (user,))
        conn.execute('DELETE FROM applications WHERE user = ?', (user,))
        conn.execute('DELETE FROM documents WHERE user = ?', (user,))
        conn.commit()
    finally:
        conn.close()


def list_tables(db_path=None):
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r['name'] for r in rows]
    finally:
        conn.close()


if __name__ == '__main__':
    version = init_db()
    print(f'DB: {config.db_path()} (schema v{version})')
    print('tables:', ', '.join(list_tables()))
