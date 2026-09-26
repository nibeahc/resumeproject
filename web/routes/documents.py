"""파일 업로드, 자소서(outputs) 및 경험(experiences) 파일 보기와 저장."""
import difflib
from io import BytesIO
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file

from lib import db, export, versions
from web.paths import (ALLOWED_EXT, output_dir, project_root, safe_path, safe_upload_name,
                       user_dir, valid_segment)

bp = Blueprint('documents', __name__)


def _db_path():
    """S8-1(버전 보존)부터 documents 테이블을 쓴다. jobs.py/matches.py 와 같은 이유로
    DB_PATH 설정을 존중한다 — 안 그러면 테스트가 실제 프로젝트 DB 를 건드리게 된다."""
    path = current_app.config.get('DB_PATH')
    db.init_db(path)
    return path


@bp.route('/upload/<name>', methods=['POST'])
def upload(name):
    kind  = request.form.get('kind', 'apply')
    if kind not in ('apply', 'logs'):
        abort(400)
    files = request.files.getlist('files')
    dest  = user_dir(name) / 'raw' / kind
    dest.mkdir(parents=True, exist_ok=True)

    saved, skipped = [], []
    for f in files:
        if not f.filename:
            continue
        fname = safe_upload_name(f.filename)
        if fname and Path(fname).suffix.lower() in ALLOWED_EXT:
            f.save(safe_path(dest, fname))
            saved.append(fname)
        else:
            skipped.append(f.filename)

    return jsonify(success=True, saved=saved, skipped=skipped)


@bp.route('/raw-fallback/<name>', methods=['POST'])
def raw_fallback(name):
    """텍스트 추출에 실패한 raw 파일의 내용을 사용자가 직접 붙여넣을 때 쓴다 (ROADMAP S2-2).

    원본 raw 파일은 절대 건드리지 않는다 (Claude.md 절대 금지). 대신 "<원본이름>.붙여넣기.md" 라는
    새 파일을 같은 폴더에 만든다. parse.py 는 이 파일도 raw/ 의 다른 파일과 똑같이 읽는다.
    """
    data = request.get_json(silent=True) or {}
    kind = data.get('kind')
    filename = data.get('filename')
    content = (data.get('content') or '').strip()

    if kind not in ('apply', 'logs'):
        abort(400)
    if not filename or not valid_segment(filename):
        abort(400)
    if len(content) < 20:
        return jsonify(success=False, error='내용이 너무 짧습니다 (최소 20자).')

    raw_dir = user_dir(name) / 'raw' / kind
    original = safe_path(raw_dir, filename)
    if not original.exists():
        return jsonify(success=False, error='원본 파일을 찾을 수 없습니다.')

    fallback_name = f'{Path(filename).stem}.붙여넣기.md'
    fallback_path = safe_path(raw_dir, fallback_name)
    fallback_path.write_text(content, encoding='utf-8')
    return jsonify(success=True, saved_as=fallback_name)


@bp.route('/output/<name>/<path:filename>')
def view_output(name, filename):
    # S7 부터 outputs/[이름]/[기업]_[직무]/*.md 처럼 한 단계 서브폴더가 생겨서 filename 에
    # "/" 가 들어올 수 있다 (path 컨버터는 슬래시를 허용한다). safe_path() 는 각 조각을
    # 개별적으로 valid_segment() 검사하므로, 여기서 미리 "/" 로 쪼개 넘긴다 — 조각 중 하나라도
    # ".." 같은 위험한 값이면 그대로 400 이 난다.
    p = safe_path(project_root() / 'outputs', name, *filename.split('/'))
    if not p.exists():
        return '파일을 찾을 수 없습니다.', 404
    return render_template('editor.html', user=name, filename=filename,
                           content=p.read_text(encoding='utf-8'), kind='output')


@bp.route('/output/<name>/<path:filename>/save', methods=['POST'])
def save_output(name, filename):
    """ROADMAP S8-1: 덮어쓰지 않고 버전을 남긴다. 경로 안전성 검사는 그대로 하되(safe_path
    가 위험한 세그먼트면 여기서 400을 낸다), 실제 파일 쓰기는 lib/versions.py 에 맡긴다 —
    직접 write_text() 하면 버전 기록 없이 덮어써진다."""
    safe_path(project_root() / 'outputs', name, *filename.split('/'))
    content = request.json.get('content', '')
    result = versions.save_version(output_dir(name), filename, content, name, db_path=_db_path())
    return jsonify(success=True, version=result.version)


@bp.route('/output/<name>/<path:filename>/versions')
def list_output_versions(name, filename):
    safe_path(project_root() / 'outputs', name, *filename.split('/'))
    history = versions.list_versions(output_dir(name), filename, name, db_path=_db_path())
    return jsonify(versions=[
        {'version': v.version, 'path': v.path, 'char_count': v.char_count, 'created_at': v.created_at}
        for v in history
    ])


@bp.route('/output/<name>/<path:filename>/diff')
def diff_output_versions(name, filename):
    """두 버전을 줄 단위로 비교한다 (ROADMAP S8-1의 "비교 가능"). ?a=1&b=3 처럼 버전 번호를 받는다."""
    safe_path(project_root() / 'outputs', name, *filename.split('/'))
    try:
        a, b = int(request.args.get('a', '')), int(request.args.get('b', ''))
    except ValueError:
        abort(400)
    vdir = versions.version_dir(output_dir(name) / filename)
    path_a, path_b = safe_path(vdir, f'v{a}.md'), safe_path(vdir, f'v{b}.md')
    if not (path_a.exists() and path_b.exists()):
        return jsonify(success=False, error='해당 버전을 찾을 수 없습니다.'), 404

    lines_a = path_a.read_text(encoding='utf-8').splitlines()
    lines_b = path_b.read_text(encoding='utf-8').splitlines()
    diff = list(difflib.unified_diff(lines_a, lines_b, fromfile=f'v{a}', tofile=f'v{b}', lineterm=''))
    return jsonify(success=True, a=a, b=b, diff=diff)


@bp.route('/output/<name>/<path:filename>/docx')
def download_output_docx(name, filename):
    """ROADMAP S8-2. PDF 는 이번 단계에서 보류(사용자 결정, 2026-09-26) — lib/export.py 참고."""
    p = safe_path(project_root() / 'outputs', name, *filename.split('/'))
    if not p.exists():
        return '파일을 찾을 수 없습니다.', 404
    doc = export.markdown_to_docx(p.read_text(encoding='utf-8'))
    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    download_name = f'{Path(filename).stem}.docx'
    return send_file(buf, as_attachment=True, download_name=download_name,
                     mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')


@bp.route('/experience/<name>/<filename>')
def view_experience(name, filename):
    p = safe_path(project_root() / 'users', name, 'experiences', filename)
    if not p.exists():
        return '파일을 찾을 수 없습니다.', 404
    return render_template('editor.html', user=name, filename=filename,
                           content=p.read_text(encoding='utf-8'), kind='experience')


@bp.route('/experience/<name>/<filename>/save', methods=['POST'])
def save_experience(name, filename):
    p = safe_path(project_root() / 'users', name, 'experiences', filename)
    p.write_text(request.json.get('content', ''), encoding='utf-8')
    return jsonify(success=True)
