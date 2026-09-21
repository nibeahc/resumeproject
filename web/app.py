from flask import Flask, render_template, request, redirect, url_for, jsonify, abort
from pathlib import Path
import re
import unicodedata

PROJECT_ROOT = Path(__file__).parent.parent
ALLOWED_EXT = {'.hwpx', '.hwp', '.docx', '.pdf', '.md', '.txt'}

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB


# ── helpers ──────────────────────────────────────────────────────────────────

_BAD_SEGMENT = re.compile(r'[/\\\x00-\x1f]')
_BAD_FILENAME_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


def valid_segment(value):
    """경로의 한 조각(사용자명, 파일명)으로 쓸 수 있는 문자열인가."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= 100
        and value not in ('.', '..')
        and not _BAD_SEGMENT.search(value)
    )


def safe_path(base: Path, *parts) -> Path:
    """base 밖으로 나가는 경로는 400. 각 조각도 단일 경로 조각이어야 한다."""
    if not all(valid_segment(x) for x in parts):
        abort(400)
    p = base.joinpath(*parts).resolve()
    if not p.is_relative_to(base.resolve()):
        abort(400)
    return p


def user_dir(name) -> Path:
    return safe_path(PROJECT_ROOT / 'users', name)


def safe_upload_name(raw):
    """업로드 파일명 정리. secure_filename 은 한글을 지우므로 쓰지 않고,
    디렉터리 성분과 위험 문자만 제거한다. 결과가 비면 None."""
    name = unicodedata.normalize('NFC', raw or '')
    name = re.split(r'[/\\]', name)[-1]          # 디렉터리 성분 제거
    name = _BAD_FILENAME_CHARS.sub('', name).strip().lstrip('.')
    return name if valid_segment(name) else None


def get_users():
    d = PROJECT_ROOT / 'users'
    return sorted([p.name for p in d.iterdir() if p.is_dir()]) if d.exists() else []


def get_experiences(user):
    d = user_dir(user) / 'experiences'
    if not d.exists():
        return []
    result = []
    for f in sorted(d.glob('*.md')):
        text = f.read_text(encoding='utf-8')
        title = (re.search(r'^# (.+)$', text, re.M) or [None, f.stem])[1]
        date  = (re.search(r'- 시기: (.+)', text) or [None, '-'])[1]
        warn  = '⚠️' if '⚠️ 오래된 경험' in text or '⚠️ STAR 보강' in text else ''
        result.append({'name': title, 'date': date, 'filename': f.name, 'warn': warn})
    return result


def get_outputs(user):
    d = safe_path(PROJECT_ROOT / 'outputs', user)
    if not d.exists():
        return []
    files = sorted(d.glob('*.md'), key=lambda f: f.stat().st_mtime, reverse=True)
    return [{'filename': f.name} for f in files]


def get_raw_files(user):
    out = {}
    for kind in ('apply', 'logs'):
        d = user_dir(user) / 'raw' / kind
        out[kind] = sorted([f.name for f in d.iterdir() if f.is_file()]) if d.exists() else []
    return out


# ── routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', users=get_users())


@app.route('/user/<name>')
def user_detail(name):
    user_dir(name)
    return render_template(
        'user.html',
        user=name,
        experiences=get_experiences(name),
        outputs=get_outputs(name),
        raw=get_raw_files(name),
    )


@app.route('/new-user', methods=['POST'])
def new_user():
    name = (request.form.get('name') or '').strip()
    if not name:
        return jsonify(success=False, error='이름을 입력해주세요.')
    if not valid_segment(name):
        return jsonify(success=False, error='이름에 사용할 수 없는 문자가 있습니다. (/ \\ .. 등)')
    for sub in ('raw/apply', 'raw/logs', 'experiences'):
        (user_dir(name) / sub).mkdir(parents=True, exist_ok=True)
    safe_path(PROJECT_ROOT / 'outputs', name).mkdir(parents=True, exist_ok=True)
    skeleton = PROJECT_ROOT / 'templates' / 'user-skeleton' / 'profile.md'
    profile_dest = user_dir(name) / 'profile.md'
    if skeleton.exists() and not profile_dest.exists():
        profile_dest.write_text(
            skeleton.read_text(encoding='utf-8').replace('[이름]', name),
            encoding='utf-8'
        )
    return jsonify(success=True, name=name, redirect=f'/profile/{name}/setup')


def parse_profile(name):
    p = user_dir(name) / 'profile.md'
    if not p.exists():
        return {}
    text = p.read_text(encoding='utf-8')

    def field(pattern):
        m = re.search(pattern, text)
        return m.group(1).strip() if m else ''

    def section(header):
        m = re.search(rf'## {header}\n(.*?)(?=\n## |\Z)', text, re.S)
        return m.group(1).strip() if m else ''

    gpa_raw = field(r'- GPA: (.+)')
    gpa_parts = gpa_raw.split('/') if '/' in gpa_raw else [gpa_raw, '4.5']

    return {
        '이름':     field(r'- 이름: (.+)'),
        '학교':     field(r'- 학교/소속: (.+)'),
        '학번':     field(r'- 학번/사번: (.+)'),
        '연락처':   field(r'- 연락처: (.+)'),
        '이메일':   field(r'- 이메일: (.+)'),
        '생년월일': field(r'- 생년월일: (.+)'),
        'gpa':      gpa_parts[0].strip(),
        'gpa_max':  gpa_parts[1].strip() if len(gpa_parts) > 1 else '4.5',
        '어학자격': section(r'어학 / 자격'),
        '목표직무': section(r'목표 직무'),
    }


@app.route('/profile/<name>/setup', methods=['GET'])
def profile_setup(name):
    if not user_dir(name).exists():
        return redirect(url_for('index'))
    return render_template('setup.html', user=name, profile=parse_profile(name))


@app.route('/profile/<name>/setup', methods=['POST'])
def profile_setup_save(name):
    f = request.form
    gpa = f.get('gpa', '').strip()
    gpa_max = f.get('gpa_max', '4.5').strip()
    content = f"""# {name} 프로필

## 기본 정보
- 이름: {f.get('이름', name).strip()}
- 학교/소속: {f.get('학교', '').strip()}
- 학번/사번: {f.get('학번', '').strip()}
- 연락처: {f.get('연락처', '').strip()}
- 이메일: {f.get('이메일', '').strip()}
- 생년월일: {f.get('생년월일', '').strip()}

## 어학 / 자격
{f.get('어학자격', '').strip()}

## 학점
- GPA: {gpa}/{gpa_max}

## 목표 직무
{f.get('목표직무', '').strip()}

## 핵심 강점

## 가치관 / 성격
"""
    (user_dir(name) / 'profile.md').write_text(content, encoding='utf-8')
    return redirect(url_for('user_detail', name=name))


@app.route('/upload/<name>', methods=['POST'])
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


@app.route('/output/<name>/<filename>')
def view_output(name, filename):
    p = safe_path(PROJECT_ROOT / 'outputs', name, filename)
    if not p.exists():
        return '파일을 찾을 수 없습니다.', 404
    return render_template('editor.html', user=name, filename=filename,
                           content=p.read_text(encoding='utf-8'), kind='output')


@app.route('/output/<name>/<filename>/save', methods=['POST'])
def save_output(name, filename):
    p = safe_path(PROJECT_ROOT / 'outputs', name, filename)
    p.write_text(request.json.get('content', ''), encoding='utf-8')
    return jsonify(success=True)


@app.route('/experience/<name>/<filename>')
def view_experience(name, filename):
    p = safe_path(PROJECT_ROOT / 'users', name, 'experiences', filename)
    if not p.exists():
        return '파일을 찾을 수 없습니다.', 404
    return render_template('editor.html', user=name, filename=filename,
                           content=p.read_text(encoding='utf-8'), kind='experience')


@app.route('/experience/<name>/<filename>/save', methods=['POST'])
def save_experience(name, filename):
    p = safe_path(PROJECT_ROOT / 'users', name, 'experiences', filename)
    p.write_text(request.json.get('content', ''), encoding='utf-8')
    return jsonify(success=True)


if __name__ == '__main__':
    app.run(debug=False, port=8080)
