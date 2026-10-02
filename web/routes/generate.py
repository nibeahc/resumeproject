"""생성 라우트 (ROADMAP S1-3, S11-7 에서 엔진 분리).

**엔진은 lib/coverletter.py 에 있다.** 이 파일은 HTTP 경계만 맡는다 — 폼/JSON 파싱,
경로 안전성 검사(web.paths), SSE 직렬화. 생성 로직이 라우트 안에 있던 동안에는
current_app 에 묶여 있어서 웹 외의 호출자가 쓸 수 없었고, 테스트도 반드시 HTTP 를
거쳐야 했다. lib/resume.py, lib/portfolio.py 는 원래부터 Flask 비의존이다.

경로 검증은 여기 남는다. web.paths.safe_path 는 flask.abort(400) 을 부르므로 lib 로
내려보낼 수 없고, 내려보내서도 안 된다 — 사용자 입력을 경로로 쓰는 1차 방어선은
HTTP 레이어의 책임이다 (tests/test_web_security.py 가 이 경계를 지킨다).

lib.coverletter 의 몇 함수를 여기서 다시 노출한다(아래 import). 테스트와 기존 호출부가
generate_route.count_chars 처럼 참조하고 있어서, 이동이 '순수 이동' 으로 남으려면 이름이
그대로 보여야 한다. stream_complete 도 모듈 레벨 import 를 유지한다 — 테스트가
patch.object(generate_route, 'stream_complete') 로 가로채는데, lib 가 자기 이름공간으로
따로 import 하면 그 patch 가 안 먹는다(lib/portfolio.py 에서 실제로 겪은 함정이다).
그래서 라우트가 complete= 로 주입한다.
"""
import json

from flask import (Blueprint, Response, current_app, jsonify, redirect, render_template,
                   request, stream_with_context, url_for)

from lib import db, matcher, portfolio, resume
from lib.coverletter import (ISSUE_CITED_UNKNOWN, ISSUE_DROPPED_OVERRIDES,  # noqa: F401 (재노출)
                             ISSUE_OVER_LIMIT, ISSUE_UNDERSHOOT, ISSUE_UNRESEARCHED_COMPANY,
                             LENGTH_TOLERANCE, MAX_CHAR_LIMIT, MAX_RETRIES, STATUS_READY,
                             STATUS_REVIEW_REQUIRED, TARGET_UNDERSHOOT, LengthSpec, build_prompt,
                             build_retry_prompt, count_chars, gather_context,
                             generate_coverletter_events, parse_answer, read_if_exists,
                             strip_placeholder_rules, validate_cited_experiences)
from lib.llm import stream_complete
from lib.resume import _safe_folder_name
from web.data import get_open_jobs
from web.paths import output_dir, project_root, trends_dir, user_dir, valid_segment

bp = Blueprint('generate', __name__)




def _db_path():
    """S7-3(bundle)이 SQLite(jobs/matches)를 건드리는 첫 지점이다. web/routes/jobs.py,
    matches.py 와 같은 이유로 DB_PATH 설정을 존중한다 — 안 그러면 테스트가 실제 프로젝트
    DB(data/app.db)를 건드리게 된다."""
    path = current_app.config.get('DB_PATH')
    db.init_db(path)
    return path

bp = Blueprint('generate', __name__)


@bp.route('/generate/<name>', methods=['GET'])
def generate_page(name):
    if not user_dir(name).exists():
        return redirect(url_for('main.index'))
    exp_dir = user_dir(name) / 'experiences'
    exp_count = len(list(exp_dir.glob('*.md'))) if exp_dir.exists() else 0
    jobs = get_open_jobs(db_path=_db_path())  # S7-3: 공고에서 불러오기 드롭다운
    return render_template('generate.html', user=name, exp_count=exp_count, jobs=jobs)



def sse(event, data):
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


def _parse_bool(data, key, default):
    """폼(문자열 'true'/'false'/'on')과 JSON(bool) 양쪽에서 와도 되게 관대하게 받는다."""
    raw = data.get(key, default)
    if isinstance(raw, str):
        return raw.strip().lower() not in ('false', '0', '')
    return bool(raw)


def _parse_use_trends(data):
    """안 보내면 기본값은 True(트렌드 반영) — 기존 동작(항상 반영)과 호환된다."""
    return _parse_bool(data, 'use_trends', default=True)


def _validate_generate_params(data):
    """company/position/question/분량 검증. (errors, company, position, question,
    length, override_files, use_trends, allow_unresearched) 를 돌려준다 — SSE 제너레이터가
    시작되기 *전에* 동기적으로 끝내야 한다 (S1-3 에서 겪은, 제너레이터 안에서 abort/에러 내면
    이미 200이 나간 뒤라 늦다는 패턴을 여기서도 피한다)."""
    company = (data.get('company') or '').strip()
    position = (data.get('position') or '').strip()
    question = (data.get('question') or '').strip()
    count_mode = (data.get('count_mode') or '포함').strip()
    if count_mode not in ('포함', '제외'):
        count_mode = '포함'

    errors = []
    if not company:
        errors.append('기업명이 없습니다.')
    elif not valid_segment(company):
        errors.append('기업명에 사용할 수 없는 문자가 있습니다. (/ \\ .. 등)')
    if not position:
        errors.append('직무명이 없습니다.')
    elif not valid_segment(position):
        errors.append('직무명에 사용할 수 없는 문자가 있습니다. (/ \\ .. 등)')
    if not question:
        errors.append('문항이 없습니다.')
    # S11-3: "글자수 미입력" 과 "제한 없음" 은 다른 상태다. 예전엔 둘 다 에러였다.
    length_mode = (data.get('length_mode') or 'bounded').strip().lower()
    if length_mode not in ('bounded', 'unlimited'):
        length_mode = 'bounded'
    char_limit = None
    if length_mode == 'bounded':
        try:
            char_limit = int(data.get('char_limit'))
            if not (0 < char_limit <= MAX_CHAR_LIMIT):
                raise ValueError
        except (TypeError, ValueError):
            char_limit = None
            errors.append(f'글자수(char_limit)가 없거나 올바르지 않습니다. '
                          f'1~{MAX_CHAR_LIMIT} 사이 숫자를 지정하거나 length_mode=unlimited 를 보내세요.')
    length = LengthSpec(mode=length_mode, hard_max_chars=char_limit, count_mode=count_mode)

    override_files = data.get('experience_files')
    if isinstance(override_files, str):
        override_files = [f.strip() for f in override_files.split(',') if f.strip()]

    use_trends = _parse_use_trends(data)
    # S11-1: 기업 자료 없이 쓰겠다는 사용자의 명시적 동의. 기본값은 False 다 —
    # 동의 없이 추론으로 채우는 것이 검토 0.1 의 핵심 지적이었다.
    allow_unresearched = _parse_bool(data, 'allow_unresearched', default=False)

    return (errors, company, position, question, length, override_files,
            use_trends, allow_unresearched)



@bp.route('/generate/<name>', methods=['POST'])
def generate(name):
    root = user_dir(name)
    data = request.get_json(silent=True) or request.form
    (errors, company, position, question, length, override_files,
     use_trends, allow_unresearched) = _validate_generate_params(data)

    if errors:
        def bad():
            yield sse('error', {'message': ' '.join(errors)})
        return Response(bad(), mimetype='text/event-stream')

    # 경로는 전부 여기서 해결해서 Path 로 넘긴다 (S11-7). lib 는 current_app 을 모른다.
    ctx = gather_context(root,
                         companies_dir=project_root() / 'companies',
                         references_dir=project_root() / 'references' / 'coverletter',
                         company=company, position=position,
                         trends_base=trends_dir(), use_trends=use_trends)

    # S11-9: 초안도 documents 에 기록하므로 이 라우트도 DB 를 건드린다. 테스트가
    # DB_PATH 를 주지 않으면 실제 data/app.db 를 쓰게 되므로 _db_path() 를 거친다.
    db_path = _db_path()

    def stream():
        for event, ev_data in generate_coverletter_events(
                name, question, root / 'experiences',
                company=company, position=position, length=length, context=ctx,
                override_filenames=override_files,
                allow_unresearched_company=allow_unresearched,
                output_dir=output_dir(name), db_path=db_path, complete=stream_complete):
            yield sse(event, ev_data)

    return Response(stream_with_context(stream()), mimetype='text/event-stream')


@bp.route('/generate/<name>/bundle', methods=['POST'])
def generate_bundle(name):
    """ROADMAP S7-3: 공고 하나를 고르면 이력서·자소서·포트폴리오 초안을 한 번에 생성한다.

    이력서(lib/resume.py)와 포트폴리오(lib/portfolio.py)는 매칭 결과(matches 테이블)의 근거
    경험을 재사용하므로, 이 공고를 아직 매칭한 적이 없으면 먼저 매칭부터 실행한다.
    """
    db_path = _db_path()

    root = user_dir(name)
    data = request.get_json(silent=True) or request.form
    job_id_raw = (data.get('job_id') or '').strip()
    if not job_id_raw.isdigit():
        return jsonify(success=False, error='공고를 선택해주세요.'), 400

    (errors, _, _, question, length, override_files,
     use_trends, allow_unresearched) = _validate_generate_params(
        {**data, 'company': 'x', 'position': 'x'})  # company/position 은 공고에서 가져오므로 폼 값 검증은 건너뜀
    errors = [e for e in errors if '기업명' not in e and '직무명' not in e]
    if errors:
        def bad():
            yield sse('error', {'message': ' '.join(errors)})
        return Response(bad(), mimetype='text/event-stream')

    job_id = int(job_id_raw)

    def stream():
        conn = db.get_conn(db_path)
        job_row = conn.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
        if not job_row:
            conn.close()
            yield sse('error', {'message': f'공고를 찾을 수 없습니다 (id={job_id}).'})
            return
        job = dict(job_row)
        company, position = job['company'], job['position']
        # resume/portfolio 와 같은 규칙으로 폴더명을 정리한다 — 공고 레코드의 회사명/직무명은
        # job_parser.commit() 이 파일시스템 안전성까지는 보지 않은 값이라 그대로 못 믿는다.
        folder_name = _safe_folder_name(company, position)

        exp_dir = root / 'experiences'
        match_row = conn.execute(
            'SELECT id FROM matches WHERE user = ? AND job_id = ? ORDER BY id DESC LIMIT 1', (name, job_id),
        ).fetchone()
        conn.close()
        if not match_row:
            yield sse('progress', {'doc': 'match', 'stage': 'start'})
            mr = matcher.match_job(name, job_id, exp_dir, db_path=db_path)
            if not mr.ok:
                yield sse('error', {'message': f'매칭 실패: {mr.error}'})
                return
            yield sse('progress', {'doc': 'match', 'stage': 'done', 'score': round(mr.score, 1), 'category': mr.category})

        yield sse('progress', {'doc': 'resume', 'stage': 'start'})
        profile_text = read_if_exists(root / 'profile.md')
        rr = resume.generate_resume(name, job_id, exp_dir, profile_text=profile_text,
                                     db_path=db_path, output_dir=output_dir(name))
        yield sse('progress', {'doc': 'resume', 'stage': 'done', 'ok': rr.ok, 'path': rr.path, 'error': rr.error})

        yield sse('progress', {'doc': 'portfolio', 'stage': 'start'})
        style_text = read_if_exists(root / 'writing-style.md')
        interest_text = read_if_exists(root / '관심직무.md')
        pr = portfolio.generate_portfolio(name, exp_dir, job_id=job_id, profile_text=profile_text,
                                           writing_style_text=style_text, interest_text=interest_text,
                                           db_path=db_path, output_dir=output_dir(name))
        yield sse('progress', {'doc': 'portfolio', 'stage': 'done', 'ok': pr.ok, 'path': pr.path, 'error': pr.error})

        coverletter_ok, coverletter_error, coverletter_path = False, None, None
        coverletter_status, coverletter_issues = None, []
        # S11-8: 공고 레코드를 통째로 넘긴다. 예전엔 여기까지 들고 와 놓고 회사명과 직무명
        # 두 문자열만 생성에 넘겼다 (검토 0.3).
        ctx = gather_context(root,
                             companies_dir=project_root() / 'companies',
                             references_dir=project_root() / 'references' / 'coverletter',
                             company=company, position=position,
                             trends_base=trends_dir(), use_trends=use_trends, job=job)
        for event, ev_data in generate_coverletter_events(
                name, question, exp_dir,
                company=company, position=position, length=length, context=ctx,
                override_filenames=override_files,
                allow_unresearched_company=allow_unresearched,
                output_dir=output_dir(name), folder_name=folder_name,
                db_path=db_path, complete=stream_complete):
            tagged = dict(ev_data)
            tagged['doc'] = 'coverletter'
            if event == 'done':
                # S11-4: 저장 성공을 품질 통과로 해석하지 않는다. 글자수를 초과했거나
                # 기업 근거가 없는 초안은 done 으로 와도 ok 가 아니다.
                coverletter_status = ev_data.get('status', STATUS_READY)
                coverletter_issues = ev_data.get('issues', [])
                coverletter_ok = (coverletter_status == STATUS_READY)
                coverletter_path = ev_data.get('rel_path')
            elif event == 'error':
                coverletter_error = ev_data.get('message')
                # error 를 그대로 흘리면 SSE 스트림이 끝나버려 이미 만든 이력서/포트폴리오
                # 결과까지 화면에 못 준다. 그래서 progress 로 감싸되 failed 임을 표시하고,
                # 최종 done 의 coverletter_ok=False 로 반드시 드러나게 한다.
                tagged['stage'] = 'failed'
            yield sse('progress', tagged)

        yield sse('done', {
            'resume_ok': rr.ok, 'resume_path': rr.path,
            'portfolio_ok': pr.ok, 'portfolio_path': pr.path,
            'coverletter_ok': coverletter_ok, 'coverletter_path': coverletter_path,
            'coverletter_error': coverletter_error,
            'coverletter_status': coverletter_status, 'coverletter_issues': coverletter_issues,
            'folder': folder_name,
        })

    return Response(stream_with_context(stream()), mimetype='text/event-stream')
