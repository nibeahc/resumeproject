"""파일 업로드, 자소서(outputs) 및 경험(experiences) 파일 보기와 저장."""
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request

from web.paths import (ALLOWED_EXT, project_root, safe_path, safe_upload_name,
                       user_dir)

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
