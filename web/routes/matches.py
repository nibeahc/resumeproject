"""매칭 결과 화면 (ROADMAP S5-2). lib/matcher.py 를 호출해 공고를 매칭하고,
사용자별 "최신" 매칭 결과만 적합/도전/부적합 순으로 정렬해 보여준다.

matches 테이블은 매칭할 때마다 새 행을 쌓는다(lib/matcher.py 참고 — 경험이 늘거나 공고가
바뀌면 재매칭해서 이력으로 비교할 수 있게 하려는 의도적인 설계). 화면에는 그중 job_id 별
가장 최근 행만 보여준다. 여러 공고를 한 번에 매칭하면 각각 LLM 호출이 하나씩 들어가 시간이
걸리므로(parse.py/generate.py 와 같은 이유), SSE 로 공고 하나 끝날 때마다 진행 상황을 보낸다.
"""
import json

from flask import Blueprint, Response, abort, current_app, render_template, request, stream_with_context

from lib import applications, db, matcher
from web.paths import user_dir

bp = Blueprint('matches', __name__)

CATEGORY_ORDER = {'적합': 0, '도전': 1, '부적합': 2}


def _db_path():
    path = current_app.config.get('DB_PATH')
    db.init_db(path)
    return path


def _exp_dir(name):
    return user_dir(name) / 'experiences'


def _open_job_ids(db_path):
    conn = db.get_conn(db_path)
    try:
        rows = conn.execute('SELECT id FROM jobs WHERE closed = 0 ORDER BY id').fetchall()
        return [r['id'] for r in rows]
    finally:
        conn.close()


def _latest_matches(user, db_path):
    """job_id 별 가장 최근 매칭 행 + 공고 정보를 합쳐, 화면에 필요한 형태로 가공한다.

    category 는 매칭 시점에만 계산되고 DB 에는 required_hit 만 저장되므로, 여기서
    matcher.classify() 로 같은 기준(임계값)을 다시 적용해 구한다 — 임계값을 두 곳에
    복제하지 않기 위해서다.

    고도화(2026-09-26): 현재 지원 상태(applications.status)도 함께 붙인다 — 매칭 결과를 보고
    바로 이 화면에서 "관심 등록"/"상태 변경"까지 할 수 있게 하려는 것(예전에는 /applications
    페이지로 따로 가서 드롭다운으로 공고를 다시 찾아야 했다).
    """
    conn = db.get_conn(db_path)
    try:
        rows = conn.execute(
            '''SELECT m.* FROM matches m
               INNER JOIN (
                   SELECT job_id, MAX(id) AS latest_id FROM matches WHERE user = ? GROUP BY job_id
               ) latest ON m.id = latest.latest_id''',
            (user,),
        ).fetchall()
        result = []
        for r in rows:
            job = conn.execute('SELECT * FROM jobs WHERE id = ?', (r['job_id'],)).fetchone()
            if not job:
                continue
            app = conn.execute('SELECT status FROM applications WHERE user = ? AND job_id = ?',
                               (user, r['job_id'])).fetchone()
            evidence = json.loads(r['evidence_json'])
            missing = json.loads(r['missing_json'])
            required = [e for e in evidence if e['kind'] == '필수']
            preferred = [e for e in evidence if e['kind'] == '우대']
            required_met = sum(1 for e in required if e['verdict'] == '충족')
            preferred_met = sum(1 for e in preferred if e['verdict'] == '충족')
            linked = sorted({e['evidence_file'] for e in evidence if e['evidence_file']})
            result.append({
                'job_id': job['id'], 'company': job['company'], 'position': job['position'],
                'deadline': job['deadline'] or '상시',
                'score': round(r['score'], 1), 'required_hit': r['required_hit'],
                'category': matcher.classify(r['required_hit']),
                'reason': f'필수 {required_met}/{len(required)}, 우대 {preferred_met}/{len(preferred)} 충족',
                'linked_experiences': linked,
                'missing': missing,
                'matched_at': r['created_at'],
                'app_status': app['status'] if app else None,
            })
        result.sort(key=lambda m: (CATEGORY_ORDER.get(m['category'], 9), -m['score']))
        return result
    finally:
        conn.close()


def sse(event, data):
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


@bp.route('/matches/<name>', methods=['GET'])
def matches_page(name):
    return render_template('matches.html', user=name, matches=_latest_matches(name, _db_path()),
                           statuses=applications.VALID_STATUSES)


@bp.route('/matches/<name>/run', methods=['POST'])
def matches_run(name):
    exp_dir = _exp_dir(name)
    db_path = _db_path()
    job_id_param = request.values.get('job_id')
    if job_id_param is not None and not job_id_param.isdigit():
        abort(400)  # SSE 제너레이터 안에서 하면 이미 200 이 나간 뒤라 늦다 (S1-3 에서 겪은 패턴)
    job_ids = [int(job_id_param)] if job_id_param else _open_job_ids(db_path)

    def stream():
        if not job_ids:
            yield sse('error', {'message': '매칭할 공고가 없습니다 (모집중인 공고를 먼저 등록하세요).'})
            return
        if not exp_dir.exists() or not any(exp_dir.glob('*.md')):
            yield sse('error', {'message': '경험 파일이 없습니다. 먼저 경험을 만들어주세요.'})
            return

        done, failed = 0, 0
        for job_id in job_ids:
            r = matcher.match_job(name, job_id, exp_dir, db_path=db_path)
            if r.ok:
                done += 1
                yield sse('progress', {
                    'job_id': job_id, 'ok': True, 'company': r.company, 'position': r.position,
                    'score': round(r.score, 1), 'category': r.category,
                })
            else:
                failed += 1
                yield sse('progress', {'job_id': job_id, 'ok': False, 'error': r.error})

        yield sse('done', {'total': len(job_ids), 'matched': done, 'failed': failed})

    return Response(stream_with_context(stream()), mimetype='text/event-stream')
