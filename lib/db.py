"""SQLite 계층. 공고, 매칭, 지원현황, 문서 버전만 이 DB 가 관리한다.

경험 DB 는 Markdown 으로 유지하며 여기에 넣지 않는다 (ROADMAP 1-A).
"""
import sqlite3
from pathlib import Path

from lib import config

SCHEMA_PATH = Path(__file__).resolve().parent / 'schema.sql'

TABLES = ('jobs', 'matches', 'applications', 'application_events', 'documents')

# PRAGMA user_version 이 곧 스키마 버전이다. 버전 N 의 마이그레이션은 MIGRATIONS[N - 1].
# 버전 1 은 schema.sql 전체. 이후 변경은 SQL 문자열을 뒤에 추가한다 (기존 항목은 수정하지 않는다).
MIGRATIONS = [
    None,  # 1: schema.sql
]


def get_conn(db_path=None):
    """외래키가 켜진 연결을 돌려준다. 행은 이름으로 접근할 수 있다 (row['company'])."""
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
