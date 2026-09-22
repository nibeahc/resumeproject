"""화면에 보여줄 데이터를 파일에서 읽는 헬퍼."""
import re

from web.paths import output_dir, project_root, user_dir


def get_users():
    d = project_root() / 'users'
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
    d = output_dir(user)
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
