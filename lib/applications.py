"""지원 상태와 이력 (ROADMAP S9-1). LLM 을 쓰지 않는 순수 DB 로직이다 — 상태 전이는
사용자가 직접 누르는 것이지 판단이 필요한 일이 아니다.

ROADMAP 이 못박은 것: **반드시 상태 변경 이력을 남긴다.** "어떤 경험을 쓴 자소서가
합격했는지"를 나중에 역산할 수 있게 하려는 것이고, 장기적으로 이 서비스에서 가장 가치
있는 데이터라고 명시되어 있다. 그래서 이 모듈은 `applications.status` 를 그냥 UPDATE
하고 끝내지 않는다 — 모든 변경을 `application_events` 에 함께 남기고, 코드로 그 이력을
재구성해서 돌려줄 수 있게 한다(모델이나 사람의 기억이 아니라 DB 가 정본이 되도록).
"""
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from lib import db

# ROADMAP 원문: "관심 → 준비중 → 지원완료 → 결과(합격/불합격/대기)". 결과를 합격/불합격/대기
# 셋으로 펼친 것은 schema.sql 의 status 컬럼 주석과 맞춘 것이다. 엄격한 상태 머신(예: 지원완료
# 에서만 합격으로 갈 수 있다)은 강제하지 않는다 — 실제로는 재지원, 상태 되돌리기 등 예외가
# 흔해서, "유효한 값 중 하나인가" 만 검증하고 순서는 사용자 판단에 맡긴다.
VALID_STATUSES = ('관심', '준비중', '지원완료', '합격', '불합격', '대기')


@dataclass
class ApplicationEvent:
    from_status: Optional[str]
    to_status: str
    note: str
    created_at: str


@dataclass
class Application:
    id: int
    user: str
    job_id: int
    status: str
    updated_at: str
    company: str = ''
    position: str = ''
    deadline: Optional[str] = None


@dataclass
class StatusResult:
    ok: bool
    application_id: Optional[int] = None
    status: str = ''
    changed: bool = False   # 실제로 상태가 바뀌었는가 (같은 상태로 "변경"하면 이력에 안 남긴다)
    error: str = ''


def _get_or_create(conn, user: str, job_id: int) -> dict:
    row = conn.execute('SELECT * FROM applications WHERE user = ? AND job_id = ?', (user, job_id)).fetchone()
    if row:
        return dict(row)
    conn.execute('INSERT INTO applications (user, job_id, status) VALUES (?, ?, ?)', (user, job_id, '관심'))
    app_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
    conn.execute(
        'INSERT INTO application_events (application_id, from_status, to_status, note) VALUES (?, NULL, ?, ?)',
        (app_id, '관심', '최초 등록'),
    )
    row = conn.execute('SELECT * FROM applications WHERE id = ?', (app_id,)).fetchone()
    return dict(row)


def set_status(user: str, job_id: int, new_status: str, note: str = '', db_path=None) -> StatusResult:
    """상태를 바꾸고, 실제로 바뀐 경우에만 application_events 에 한 행을 남긴다.

    (user, job_id) 조합의 applications 행이 아직 없으면 '관심' 상태로 먼저 만든 뒤(최초
    등록 이벤트도 함께 남는다) 그 위에 요청된 상태로 바꾼다.
    """
    if new_status not in VALID_STATUSES:
        return StatusResult(ok=False, error=f'유효하지 않은 상태입니다: {new_status!r} (허용: {", ".join(VALID_STATUSES)})')

    conn = db.get_conn(db_path)
    try:
        app = _get_or_create(conn, user, job_id)
        current = app['status']
        if current == new_status:
            # _get_or_create() 가 새로 만든 행이었다면(최초 등록 이벤트까지 포함해서) 여기서
            # commit 하지 않으면 conn.close() 시 통째로 롤백된다 — "상태가 안 바뀌었으니 아무
            # 것도 안 썼다"가 아니라 "이미 썼는데 커밋을 안 했다"인 경우라 반드시 커밋해야 한다.
            conn.commit()
            return StatusResult(ok=True, application_id=app['id'], status=current, changed=False)

        conn.execute('UPDATE applications SET status = ?, updated_at = datetime(\'now\', \'localtime\') WHERE id = ?',
                     (new_status, app['id']))
        conn.execute(
            'INSERT INTO application_events (application_id, from_status, to_status, note) VALUES (?, ?, ?, ?)',
            (app['id'], current, new_status, note or ''),
        )
        conn.commit()
        return StatusResult(ok=True, application_id=app['id'], status=new_status, changed=True)
    finally:
        conn.close()


def get_history(user: str, job_id: int, db_path=None) -> List[ApplicationEvent]:
    conn = db.get_conn(db_path)
    try:
        app = conn.execute('SELECT id FROM applications WHERE user = ? AND job_id = ?', (user, job_id)).fetchone()
        if not app:
            return []
        rows = conn.execute(
            'SELECT * FROM application_events WHERE application_id = ? ORDER BY created_at ASC, id ASC',
            (app['id'],),
        ).fetchall()
        return [ApplicationEvent(from_status=r['from_status'], to_status=r['to_status'],
                                  note=r['note'] or '', created_at=r['created_at']) for r in rows]
    finally:
        conn.close()


def list_applications(user: str, db_path=None) -> List[Application]:
    """사용자의 지원 현황 전체를 공고 정보와 함께 최근 갱신순으로 돌려준다."""
    conn = db.get_conn(db_path)
    try:
        rows = conn.execute(
            '''SELECT a.*, j.company, j.position, j.deadline FROM applications a
               JOIN jobs j ON j.id = a.job_id
               WHERE a.user = ? ORDER BY a.updated_at DESC''',
            (user,),
        ).fetchall()
        return [Application(id=r['id'], user=r['user'], job_id=r['job_id'], status=r['status'],
                             updated_at=r['updated_at'], company=r['company'], position=r['position'],
                             deadline=r['deadline']) for r in rows]
    finally:
        conn.close()


IN_PROGRESS_STATUSES = ('준비중',)
AWAITING_RESULT_STATUSES = ('지원완료', '대기')
CLOSING_SOON_DAYS = 7  # ROADMAP S9-2: "마감 임박" 기준


def dashboard_summary(user: str, days: int = CLOSING_SOON_DAYS, db_path=None) -> dict:
    """ROADMAP S9-2 대시보드용 요약. 마감 임박 / 준비중 / 결과 대기 세 갈래로 나눠서 돌려준다.

    새 쿼리를 짜지 않고 list_applications() 하나를 코드로 세 번 필터링한다 — 사용자 한 명의
    지원 건수는 많아야 수십 개 수준이라, DB 쪽에서 따로 최적화할 이유가 없다.
    """
    apps = list_applications(user, db_path=db_path)
    today = date.today()

    closing_soon = []
    for a in apps:
        if not a.deadline or a.status in ('합격', '불합격'):
            continue
        try:
            d = date.fromisoformat(a.deadline)
        except ValueError:
            continue  # "상시" 등 날짜가 아닌 값 — 마감 임박 판단에서 제외
        days_left = (d - today).days
        if 0 <= days_left <= days:
            closing_soon.append((days_left, a))
    closing_soon = [a for _, a in sorted(closing_soon, key=lambda pair: pair[0])]

    return {
        'closing_soon': closing_soon,
        'in_progress': [a for a in apps if a.status in IN_PROGRESS_STATUSES],
        'awaiting_result': [a for a in apps if a.status in AWAITING_RESULT_STATUSES],
    }


def list_available_jobs(user: str, db_path=None) -> List[dict]:
    """아직 지원 현황에 등록되지 않은(관심 등록조차 안 한) 공고 목록 — "관심 등록" 후보용."""
    conn = db.get_conn(db_path)
    try:
        rows = conn.execute(
            '''SELECT j.id, j.company, j.position FROM jobs j
               WHERE j.closed = 0 AND j.id NOT IN (
                   SELECT job_id FROM applications WHERE user = ?
               ) ORDER BY j.fetched_at DESC''',
            (user,),
        ).fetchall()
        return [{'id': r['id'], 'company': r['company'], 'position': r['position']} for r in rows]
    finally:
        conn.close()
