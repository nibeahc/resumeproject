"""파일 업로드, 자소서(outputs) 및 경험(experiences) 파일 보기와 저장."""
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request

from web.paths import (ALLOWED_EXT, project_root, safe_path, safe_upload_name,
                       user_dir, valid_segment)

bp = Blueprint('documents', __name__)


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


@bp.route('/output/<name>/<filename>')
def view_output(name, filename):
    p = safe_path(project_root() / 'outputs', name, filename)
    if not p.exists():
        return '파일을 찾을 수 없습니다.', 404
    return render_template('editor.html', user=name, filename=filename,
                           content=p.read_text(encoding='utf-8'), kind='output')


@bp.route('/output/<name>/<filename>/save', methods=['POST'])
def save_output(name, filename):
    p = safe_path(project_root() / 'outputs', name, filename)
    p.write_text(request.json.get('content', ''), encoding='utf-8')
    return jsonify(success=True)


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
