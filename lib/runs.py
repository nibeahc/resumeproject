"""실행 기록. 생성과 수정이 **실제로 어느 경로로 돌았고 무엇을 검사했는지**를 남긴다.

왜 필요한가 (2차 설계검토 4장) — "웹과 CLI 가 쓸 수 있는 공통 함수가 있다" 와
"실제 자소서 작성이 그 함수를 거친다" 는 다른 문장이다. 2026-10-02 에 퀴네앤드나겔
자소서는 lib/coverletter.py 를 **한 번도 거치지 않고** versions.save_version 만
불러 저장됐는데, 이틀 동안 아무도 몰랐다. 되물을 자리가 없었기 때문이다.

이 모듈이 답할 수 있게 하는 질문은 세 가지다.

  1. 이 문서는 실제로 검사를 거쳤나          → status, quality_check, reviewed
  2. 검사 뒤에 본문이 바뀌지는 않았나         → content_hash 와 현재 본문 대조
  3. 어느 경로로 돌았나                      → surface

content_hash 를 본문에 묶는 것이 핵심이다. 검사 이후 본문이 바뀌면 이전 통과 결과는
더 이상 그 본문에 대한 것이 아니다.
"""
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from lib import db

SURFACES = ('web', 'cli', 'script')


def new_run_id() -> str:
    return uuid.uuid4().hex


def content_hash(text: str) -> str:
    """검사 대상 본문의 지문. lib/job_parser.py 의 content_hash 와 같은 방식이다."""
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()


@dataclass
class RunRecord:
    run_id: str
    user: str
    surface: str
    workflow: str
    company: str = ''
    position: str = ''
    question: str = ''
    content_hash: str = ''
    status: str = ''
    issues: List[str] = field(default_factory=list)
    quality_check: bool = False
    reviewed: bool = False
    inputs: dict = field(default_factory=dict)


def record(run: RunRecord, db_path=None) -> None:
    """실행 기록 한 줄을 남긴다. 같은 run_id 로 다시 부르면 덮어쓴다."""
    if run.surface not in SURFACES:
        raise ValueError(f'알 수 없는 실행 경로: {run.surface} (허용: {", ".join(SURFACES)})')
    conn = db.get_conn(db_path)
    try:
        conn.execute(
            'INSERT INTO runs (run_id, user, surface, workflow, company, position, question,'
            ' content_hash, status, issues, quality_check, reviewed, inputs)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)'
            ' ON CONFLICT(run_id) DO UPDATE SET'
            '  content_hash=excluded.content_hash, status=excluded.status,'
            '  issues=excluded.issues, quality_check=excluded.quality_check,'
            '  reviewed=excluded.reviewed, inputs=excluded.inputs',
            (run.run_id, run.user, run.surface, run.workflow, run.company, run.position,
             run.question, run.content_hash, run.status, json.dumps(run.issues, ensure_ascii=False),
             int(run.quality_check), int(run.reviewed),
             json.dumps(run.inputs, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def get(run_id: str, db_path=None) -> Optional[dict]:
    conn = db.get_conn(db_path)
    try:
        row = conn.execute('SELECT * FROM runs WHERE run_id = ?', (run_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    out = dict(row)
    out['issues'] = json.loads(out['issues'] or '[]')
    out['inputs'] = json.loads(out['inputs'] or '{}')
    out['quality_check'] = bool(out['quality_check'])
    out['reviewed'] = bool(out['reviewed'])
    return out


def for_document(rel_version_path: str, db_path=None) -> Optional[dict]:
    """저장된 문서 버전이 어느 실행에서 나왔는지 돌려준다. 손으로 쓴 문서면 None."""
    conn = db.get_conn(db_path)
    try:
        row = conn.execute(
            'SELECT run_id FROM documents WHERE path = ? ORDER BY id DESC LIMIT 1',
            (rel_version_path,)).fetchone()
    finally:
        conn.close()
    if row is None or not row['run_id']:
        return None
    return get(row['run_id'], db_path=db_path)


def verify(run_id: str, current_text: str, db_path=None) -> dict:
    """그 실행의 검사 결과가 **지금 본문에 대해서도** 유효한지 본다.

    돌려주는 dict:
      found     실행 기록이 있는가 (없으면 공통 실행기를 안 거쳤다는 뜻)
      unchanged 검사한 본문과 지금 본문이 같은가
      stale     검사 뒤에 본문이 바뀌었는가 (found 인데 unchanged 가 아닌 경우)
    """
    run = get(run_id, db_path=db_path)
    if run is None:
        return {'found': False, 'unchanged': False, 'stale': False, 'run': None}
    same = run['content_hash'] == content_hash(current_text)
    return {'found': True, 'unchanged': same, 'stale': not same, 'run': run}
