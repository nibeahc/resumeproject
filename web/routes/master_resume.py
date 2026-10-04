"""마스터 이력서 화면과 생성 (S11-24/25).

기업 맞춤 서류를 쓰기 전에 가장 먼저 만드는 문서다. 조립은 lib/master_resume.py 가
하고, 이 모듈은 HTTP 경계만 담당한다 (lib 는 Flask 를 모른다 — S11-7 과 같은 구조).
"""
from flask import Blueprint, Response, render_template, request, stream_with_context

from lib.master_resume import MASTER_FILENAME, build_master_resume_events, collect
from lib.llm import stream_complete
from web.paths import user_dir
from web.routes.generate import _db_path, sse

bp = Blueprint('master_resume', __name__)


@bp.route('/master-resume/<name>', methods=['GET'])
def master_resume_page(name):
    root = user_dir(name)
    exp_dir = root / 'experiences'
    existing = root / MASTER_FILENAME
    resume = collect(root, exp_dir) if exp_dir.exists() else None
    return render_template(
        'master_resume.html', user=name,
        entry_count=len(resume.entries) if resume else 0,
        existing=existing.read_text(encoding='utf-8') if existing.exists() else '',
    )


@bp.route('/master-resume/<name>', methods=['POST'])
def build(name):
    root = user_dir(name)
    exp_dir = root / 'experiences'
    db_path = _db_path()
    # 미리보기는 파일을 쓰지 않는다. 사람이 보고 저장을 누를 수 있어야 한다.
    write = str((request.get_json(silent=True) or request.form).get('write', '')).lower() \
        in ('1', 'true', 'yes', 'on')

    def stream():
        for event, data in build_master_resume_events(
                name, root, exp_dir, db_path=db_path, surface='web',
                write=write, complete=stream_complete):
            yield sse(event, data)

    return Response(stream_with_context(stream()), mimetype='text/event-stream')
