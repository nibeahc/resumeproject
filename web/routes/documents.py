"""파일 업로드, 자소서(outputs) 및 경험(experiences) 파일 보기와 저장."""
import difflib
import shutil
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file

from lib import db, export, versions
from web.data import build_case
from web.paths import (ALLOWED_EXT, output_dir, project_root, safe_path, safe_upload_name,
                       trash_dir, user_dir, valid_segment)

bp = Blueprint('documents', __name__)

# 고도화(2026-09-27, 사용자 요청): "추가도 파일 업로드나 대화 없이도 추가 가능하게" — 빈
# 템플릿으로 경험을 바로 만든다. elicit.py 의 _TEMPLATE_PATH 와 같은 이유로 project_root()
# (테스트마다 바뀌는 PROJECT_ROOT) 가 아니라 실제 저장소의 templates/ 를 직접 가리킨다
# (템플릿은 프로젝트 고정 자산이지 사용자별 데이터가 아니다).
_EXPERIENCE_TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / 'templates' / 'experience-entry.md'


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


@bp.route('/case/<name>/<folder>')
def view_case(name, folder):
    """지원 건 하나를 한 페이지로 본다 (사용자 요청, 2026-10-06).

    "항목은 건별로 하나만, 한 페이지에 초안이 먼저 오고 그 아래에 최종본." 그래서 폴더
    안의 파일을 따로따로 열던 목록 대신 이 화면이 지원 건의 입구가 된다. 초안은 읽기
    전용으로 보여주고(고치려면 기존 편집 화면으로 간다), 최종본은 여기서 바로 고친다.
    """
    safe_path(project_root() / 'outputs', name, folder)
    case = build_case(name, folder)
    if case is None:
        return '지원 건을 찾을 수 없습니다.', 404
    return render_template('case.html', user=name, case=case)


@bp.route('/case/<name>/<folder>/final', methods=['POST'])
def create_final(name, folder):
    """초안을 그대로 복사해 최종본.md 를 만든다.

    빈 문서가 아니라 복사본으로 시작한다 — 최종본은 초안을 퇴고해서 나오는 것이지
    처음부터 다시 쓰는 글이 아니다. 직접 write_text 하지 않고 save_version 을 거친다
    (Claude.md "실행 기록 없이 저장하지 않는다"). 손으로 만든 것이므로 run_id 는 없다.
    """
    safe_path(project_root() / 'outputs', name, folder)
    case = build_case(name, folder)
    if case is None:
        return jsonify(success=False, error='지원 건을 찾을 수 없습니다.'), 404
    if case['final']:
        return jsonify(success=False, error='이미 최종본이 있습니다.'), 400
    if not case['draft']:
        return jsonify(success=False, error='복사할 초안이 없습니다.'), 400

    result = versions.save_version(output_dir(name), case['final_filename'],
                                   case['draft']['content'], name, db_path=_db_path())
    return jsonify(success=True, version=result.version, filename=case['final_filename'])


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


@bp.route('/experience/<name>/<filename>/delete', methods=['POST'])
def delete_experience(name, filename):
    """경험 삭제(2026-09-27, 사용자 요청). 사용자 삭제(/delete-user)와 같은 안전장치를
    쓴다 — 실제 rm 대신 _trash/ 로 옮겨서, 확인창을 잘못 눌러도 되돌릴 여지를 남긴다."""
    p = safe_path(project_root() / 'users', name, 'experiences', filename)
    if not p.exists():
        return jsonify(success=False, error='파일을 찾을 수 없습니다.'), 404
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    dest_dir = trash_dir() / f'{name}__experience__{ts}'
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(dest_dir / filename))
    return jsonify(success=True)


@bp.route('/experience/<name>/new', methods=['POST'])
def new_experience(name):
    """경험을 파일 업로드나 대화 없이 빈 템플릿으로 바로 추가한다(2026-09-27, 사용자 요청:
    "추가도 파일 업로드나 대화 없이도 추가 가능하게, 템플릿만 포함된 백지로"). 활동명만 받아
    templates/experience-entry.md 의 빈 문서를 만들고, 나머지는 편집 화면에서 사용자가
    직접 채운다."""
    title = (request.form.get('title') or '').strip()
    if not title or not valid_segment(title):
        return jsonify(success=False, error='활동명을 입력해주세요.')

    exp_dir = user_dir(name) / 'experiences'
    exp_dir.mkdir(parents=True, exist_ok=True)

    stem = safe_upload_name(title) or '새경험'
    year = date.today().year
    filename = f'{year}-{stem}.md'
    n = 2
    while (exp_dir / filename).exists():
        filename = f'{year}-{stem}_{n}.md'
        n += 1

    body = _EXPERIENCE_TEMPLATE_PATH.read_text(encoding='utf-8').replace('[활동명]', title, 1)
    safe_path(exp_dir, filename).write_text(body, encoding='utf-8')
    return jsonify(success=True, filename=filename)
