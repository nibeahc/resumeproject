"""공고 등록 화면 (ROADMAP S4-3). lib/job_parser.py 의 preview()/commit() 을 그대로 쓴다.

S3-4(elicit)와 같은 이유로 서버 세션을 쓰지 않는다: preview() 결과(마크다운, 필수/우대조건,
회사명/직무명 등)를 클라이언트가 통째로 들고 있다가, 사용자가 확인(또는 수정)한 뒤 그대로
/jobs/save 에 다시 보낸다. 서버는 그 사이 아무 상태도 기억하지 않는다.
"""
import json

from flask import Blueprint, current_app, jsonify, render_template, request

from lib import db, job_parser
from web.paths import project_root

bp = Blueprint('jobs', __name__)


def _jobs_dir():
    return project_root() / 'jobs'


def _db_path():
    """이 앱 인스턴스가 쓸 DB 경로. None 이면 db.get_conn()/init_db() 가 lib.config.db_path() 로 대체한다.

    매 요청마다 init_db() 를 부르는 건 이 블루프린트뿐이다 (jobs 테이블을 처음 쓰는 라우트라서) —
    idempotent 하므로(이미 최신 버전이면 아무 것도 안 함) 비용은 PRAGMA 조회 한 번 정도다.
    테스트가 create_app({'DB_PATH': tmp_path}) 로 주입하면 실제 프로젝트 DB 는 건드리지 않는다.
    """
    path = current_app.config.get('DB_PATH')
    db.init_db(path)
    return path


def _list_jobs():
    conn = db.get_conn(_db_path())
    try:
        rows = conn.execute(
            'SELECT id, company, position, deadline, employment_type, required_json, preferred_json, '
            'raw_path, closed FROM jobs ORDER BY fetched_at DESC'
        ).fetchall()
    finally:
        conn.close()
    result = []
    for r in rows:
        result.append({
            'id': r['id'], 'company': r['company'], 'position': r['position'],
            'deadline': r['deadline'] or '상시', 'employment_type': r['employment_type'] or '-',
            'required_count': len(json.loads(r['required_json'])),
            'preferred_count': len(json.loads(r['preferred_json'])),
            'path': r['raw_path'], 'closed': bool(r['closed']),
        })
    return result


def _result_to_json(r: job_parser.JobResult):
    return {
        'ok': r.ok, 'duplicate': r.duplicate, 'saved': r.saved, 'job_id': r.job_id,
        'source_job_id': r.source_job_id, 'path': r.path, 'company': r.company, 'position': r.position,
        'deadline': r.deadline, 'employment_type': r.employment_type,
        'required': r.required, 'preferred': r.preferred, 'markdown': r.markdown,
        'raw_text': r.raw_text, 'content_hash': r.content_hash, 'url': r.url, 'error': r.error,
    }


def _result_from_json(data: dict) -> job_parser.JobResult:
    return job_parser.JobResult(
        ok=True, duplicate=False, saved=False,
        company=(data.get('company') or '').strip(), position=(data.get('position') or '').strip(),
        deadline=data.get('deadline'), employment_type=data.get('employment_type'),
        required=[x for x in (data.get('required') or []) if isinstance(x, str)],
        preferred=[x for x in (data.get('preferred') or []) if isinstance(x, str)],
        markdown=data.get('markdown') or '', raw_text=data.get('raw_text') or '',
        content_hash=data.get('content_hash') or '', url=data.get('url') or '',
    )


@bp.route('/jobs', methods=['GET'])
def jobs_page():
    return render_template('jobs.html', jobs=_list_jobs())


@bp.route('/jobs/preview', methods=['POST'])
def jobs_preview():
    data = request.get_json(silent=True) or {}
    raw_text = data.get('raw_text') or ''
    url = data.get('url') or ''
    company_hint = data.get('company_hint') or ''
    position_hint = data.get('position_hint') or ''

    r = job_parser.preview(raw_text, url=url, company_hint=company_hint, position_hint=position_hint,
                           db_path=_db_path())
    return jsonify(**_result_to_json(r))


@bp.route('/jobs/save', methods=['POST'])
def jobs_save():
    data = request.get_json(silent=True) or {}
    if not (data.get('raw_text') or '').strip() or not (data.get('markdown') or '').strip():
        return jsonify(ok=False, error='저장할 미리보기 내용이 없습니다. 다시 파싱해주세요.'), 400

    preview_result = _result_from_json(data)
    r = job_parser.commit(preview_result, jobs_dir=_jobs_dir(), db_path=_db_path())
    return jsonify(**_result_to_json(r))
