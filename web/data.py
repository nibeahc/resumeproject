"""화면에 보여줄 데이터를 파일에서 읽는 헬퍼."""
import re

from lib import db
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
    """outputs/[이름]/ 아래 결과물을 나열한다.

    S1~S1-4 시절엔 outputs/[이름]/[기업]_[직무].md 처럼 평평하게 저장했다. S7(이력서/
    포트폴리오/자소서)부터는 outputs/[이름]/[기업]_[직무]/*.md 서브폴더에 모아 쌓는다
    (한 지원 건의 문서 3종이 한 폴더에 있어야 S7-3 일괄 생성이 자연스럽다). 두 형태가
    실제로 공존할 수 있으므로(과거에 만든 파일이 남아있다) 둘 다 찾는다.
    """
    d = output_dir(user)
    if not d.exists():
        return []
    files = list(d.glob('*.md')) + list(d.glob('*/*.md'))
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [{'filename': f.relative_to(d).as_posix()} for f in files]


def get_open_jobs(db_path=None):
    """generate.html 의 "공고에서 불러오기" 드롭다운용 (S7-3). 마감 안 된 공고만, 최신순.
    jobs 는 사용자별이 아니라 전역이라 project_root/user_dir 을 거치지 않고 DB 를 직접 본다."""
    conn = db.get_conn(db_path)
    try:
        rows = conn.execute(
            'SELECT id, company, position FROM jobs WHERE closed = 0 ORDER BY fetched_at DESC'
        ).fetchall()
        return [{'id': r['id'], 'company': r['company'], 'position': r['position']} for r in rows]
    finally:
        conn.close()


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


def parse_writing_rules(name):
    """writing-rules.md 를 setup.html 폼 필드용으로 읽는다.
    templates/user-skeleton/writing-rules.md 의 섹션 구성(구조/금지 표현/선호 표현/기타)과
    parse-experiences.md 가 자동 생성하는 초안이 이 구조를 따른다 (S3-5)."""
    p = user_dir(name) / 'writing-rules.md'
    if not p.exists():
        return {}
    text = p.read_text(encoding='utf-8')

    def section(header):
        m = re.search(rf'## {header}\n(.*?)(?=\n## |\Z)', text, re.S)
        return m.group(1).strip() if m else ''

    return {
        '구조':      section('구조'),
        '금지표현':  section('금지 표현'),
        '선호표현':  section('선호 표현'),
        '기타':      section('기타'),
    }
