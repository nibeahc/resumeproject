"""화면에 보여줄 데이터를 파일에서 읽는 헬퍼."""
import difflib
import re
from datetime import datetime
from urllib.parse import quote

from lib import db
from web.paths import output_dir, project_root, user_dir


def get_users():
    d = project_root() / 'users'
    return sorted([p.name for p in d.iterdir() if p.is_dir()]) if d.exists() else []


_DATE_PREFIX = re.compile(r'(\d{4})(?:\.(\d{1,2}))?(?:\.(\d{1,2}))?')


def _start_date_key(date_text):
    """'- 시기: ...' 값에서 시작 시기를 뽑아 정렬 키로 쓴다. skills/query-experiences.md 의
    '시기순(오래된 순)' 타임라인 규칙과 같은 기준(연도 → 월 → 일, 없으면 가장 이른 것으로 간주)
    을 대시보드 목록에도 맞춘다. 파싱 자체가 안 되면(형식이 특이한 경우) 맨 뒤로 보낸다."""
    m = _DATE_PREFIX.search(date_text)
    if not m:
        return (9999, 0, 0)
    year, month, day = m.groups()
    return (int(year), int(month or 1), int(day or 1))


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
    result.sort(key=lambda e: _start_date_key(e['date']))
    return result


# 자소서 결과물 화면은 "지원 한 건 = 한 항목" 이다 (사용자 요청, 2026-10-06).
# 예전엔 폴더 안의 .md 를 전부 평평하게 나열해서, 한 지원 건이 근거표·이력서·포트폴리오까지
# 네댓 줄로 흩어졌다. 이제 폴더 하나를 한 줄로 접고, 그 안을 역할로 나눈다.
#   초안   = 생성기가 쓴 글 (lib/coverletter.py 는 '자소서.md' 로 저장한다)
#   최종본 = 사람이 확정한 글
#   그 외  = 부속 파일(근거표·이력서·포트폴리오 등). 목록에는 올리지 않고 건 페이지
#            맨 아래에서만 연다 — outputs/ 는 git 추적 대상이 아니라서, 화면에서
#            아예 빼버리면 다시 찾아갈 경로가 없어진다.
DRAFT_FILENAMES = ('자소서.md', '초안_AI.md', '초안.md')
FINAL_FILENAME = '최종본.md'


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _is_version_dir(d):
    """lib/versions.py 가 만든 버전 폴더('자소서/v1.md')인가.

    형제에 같은 이름의 .md 가 있으면 버전 폴더다. 지원 건 폴더로 세면 안 된다 —
    옛 평평한 구조('기업_직무.md')의 버전 폴더가 '기업_직무/' 라서 겉모습이 같다.
    """
    return (d.parent / f'{d.name}.md').exists()


def _case_title(folder):
    """'기업_직무' 를 되돌려 읽는다 (lib/resume._safe_folder_name 의 역방향)."""
    company, sep, position = folder.partition('_')
    return f'{company} · {position}' if sep and position else folder


def _split_case_files(files):
    """폴더 안의 .md 를 (초안, 최종본, 그 외) 로 나눈다."""
    by_name = {f.name: f for f in files}
    final = by_name.get(FINAL_FILENAME)
    draft = next((by_name[n] for n in DRAFT_FILENAMES if n in by_name), None)
    if draft is None and final is None and files:
        # 초안도 최종본도 없는 폴더(이력서만 만든 건 등)는 가장 최근 파일을 초안 자리에 둔다.
        # 빈 화면을 보여주느니 무엇이 들어 있는지는 보여준다.
        draft = max(files, key=_mtime)
    others = [f for f in files if f is not draft and f is not final]
    return draft, final, others


def _file_entry(base, path):
    return {
        'name': path.name,
        # /output/<user>/<path:filename> 라우트에 그대로 붙일 수 있는 형태 (S11-5 와 같은 규약)
        'filename': path.relative_to(base).as_posix(),
        'updated': datetime.fromtimestamp(_mtime(path)).strftime('%Y-%m-%d %H:%M'),
    }


# 지난 버전에서 "다음 버전에서 고쳐진 부분" 을 표시하는 기호 (사용자 요청, 2026-10-12).
# 마크다운 원문에 끼워 넣고, 화면(case.html)이 렌더링한 뒤 빨간 볼드 태그로 바꾼다.
# 사용자 문서에 나올 일이 없는 유니코드 사용자 영역 문자를 쓴다.
MARK_OPEN, MARK_CLOSE = '\ue000', '\ue001'

_WORD = re.compile(r'\S+')
# 본문이 아닌 줄 — 비교하지도, 표시하지도 않는다 (사용자 요청, 2026-10-12: "공고·기업 정보 같은
# 머리말, 참조한 경험, 글자수 등은 빼고 내용에만"). 제목, 인용(공고·마감 머리말), 구분선, 표,
# '글자수: …' '참조한 경험: …' '작성일: …' '**강점선택**: …' 같은 '항목: 값' 줄.
_META_LINE = re.compile(r'^\s*(#|>|\||-{3,}\s*$|\**[가-힣A-Za-z][가-힣A-Za-z ]{0,11}\**\s*[:：])')


def _content_words(text):
    """본문 줄의 어절만 (줄 번호, 시작, 끝) 과 함께 뽑는다."""
    words = []
    for ln, line in enumerate(text.split('\n')):
        if not line.strip() or _META_LINE.match(line):
            continue
        words.extend((ln, m.start(), m.end(), m.group()) for m in _WORD.finditer(line))
    return words


def mark_changes(old, new):
    """old 에서 new 로 넘어가며 고쳐지거나 지워진 본문을 MARK_OPEN/CLOSE 로 감싼 old 를 돌려준다.

    띄어쓰기 단위(어절)로 비교한다 — 글자 단위는 조사 하나 바뀐 데도 조각조각 칠해져 읽기 어렵다.
    본문 줄끼리만 비교하므로 머리말·글자수·참조한 경험은 바뀌어도 표시되지 않는다.
    줄바꿈 위치가 달라진 것도 수정으로 치지 않는다. 표시는 줄을 넘지 않는다(렌더러가 줄 단위로
    읽는다). 바뀐 곳 수(표시 덩어리 수)도 함께 돌려준다.
    """
    a, b = _content_words(old), _content_words(new)
    changed = set()
    sm = difflib.SequenceMatcher(None, [w[3] for w in a], [w[3] for w in b], autojunk=False)
    for tag, i1, i2, _, _ in sm.get_opcodes():
        if tag in ('replace', 'delete'):
            changed.update(range(i1, i2))

    # 줄마다, 바뀐 어절이 연달아 있으면 사이 공백까지 한 덩어리로 감싼다
    spans = {}   # 줄 번호 -> [(시작, 끝)]
    for i in sorted(changed):
        ln, st, en, _ = a[i]
        line_spans = spans.setdefault(ln, [])
        if line_spans and i - 1 in changed and a[i - 1][0] == ln:
            line_spans[-1] = (line_spans[-1][0], en)
        else:
            line_spans.append((st, en))

    lines = old.split('\n')
    for ln, line_spans in spans.items():
        line = lines[ln]
        for st, en in reversed(line_spans):
            line = line[:st] + MARK_OPEN + line[st:en] + MARK_CLOSE + line[en:]
        lines[ln] = line
    return '\n'.join(lines), sum(len(v) for v in spans.values())


def _version_files(vdir):
    """'자소서/' 같은 버전 폴더의 v1.md, v2.md ... 를 번호순으로."""
    if not vdir.is_dir():
        return []
    found = [(int(m.group(1)), f) for f in vdir.glob('v*.md')
             for m in [re.match(r'^v(\d+)\.md$', f.name)] if m]
    return [f for _, f in sorted(found)]


def _history_chain(draft, final):
    """과거 → 최신 순서의 문서 이력. 맨 끝이 지금 화면 맨 위에 뜨는 글(최종본, 없으면 초안)이다.

    초안 버전들(자소서/v1..) → 초안 파일 → 최종본 버전들(최종본/v1..) → 최종본 파일.
    save_version 은 파일과 마지막 버전을 같은 내용으로 남기고, 최종본 v1 은 초안을 그대로
    복사한 것이라, 바로 앞과 내용이 같은 항목은 빼서 같은 글이 두 번 뜨지 않게 한다.
    """
    chain = []
    for doc, label in ((draft, '초안'), (final, '최종본')):
        if doc is None:
            continue
        for f in _version_files(doc.parent / doc.stem):
            chain.append((f, f'{label} {f.stem}'))
        chain.append((doc, label))

    items = []
    for path, label in chain:
        content = path.read_text(encoding='utf-8')
        if items and items[-1]['raw'] == content:
            items[-1] = {**items[-1], 'path': path, 'label': label}   # 같은 글이면 최신 이름으로
            continue
        items.append({'path': path, 'label': label, 'raw': content})
    return items


def build_case(user, folder):
    """지원 건 하나의 화면 데이터 (사용자 요청 2026-10-06, 2026-10-12).

    맨 위에 최종본을 따로 띄우고, 그 아래로 초안부터 과거 → 최신 순서로 지난 버전을 깐다.
    지난 버전마다 바로 다음 버전에서 고쳐진 부분을 표시해 둔다 ('history').

    폴더가 없으면 None. 경로 안전성 검사는 호출하는 라우트(web.paths.safe_path)의 몫이다.
    """
    base = output_dir(user)
    case_dir = base / folder
    if not case_dir.is_dir():
        return None
    files = sorted(case_dir.glob('*.md'))
    draft, final, others = _split_case_files(files)

    def doc(path):
        if path is None:
            return None
        entry = _file_entry(base, path)
        entry['content'] = path.read_text(encoding='utf-8')
        entry['chars'] = len(entry['content'])
        return entry

    chain = _history_chain(draft, final)
    # 최종본이 있으면 체인의 마지막(=최종본)은 맨 위 블록이 따로 보여주므로 이력에서 뺀다.
    # 최종본이 없으면 체인 전체가 이력이고, 마지막 항목(가장 최근 초안)은 비교할 다음 버전이 없다.
    past = chain[:-1] if final is not None else chain
    history = []
    for i, item in enumerate(past):
        nxt = chain[i + 1]['raw'] if i + 1 < len(chain) else None
        marked, n = mark_changes(item['raw'], nxt) if nxt is not None else (item['raw'], 0)
        entry = _file_entry(base, item['path'])
        entry.update(label=item['label'], content=marked, chars=len(item['raw']), changes=n)
        history.append(entry)

    return {
        'folder': folder,
        'title': _case_title(folder),
        'draft': doc(draft),
        'final': doc(final),
        'history': history,
        'others': [_file_entry(base, f) for f in others],
        'final_filename': f'{folder}/{FINAL_FILENAME}',
    }


def get_outputs(user):
    """outputs/[이름]/ 아래 결과물을 **지원 건 단위로** 나열한다 (한 건 = 한 항목).

    S1~S1-4 시절엔 outputs/[이름]/[기업]_[직무].md 처럼 평평하게 저장했다. S7(이력서/
    포트폴리오/자소서)부터는 outputs/[이름]/[기업]_[직무]/*.md 서브폴더에 모아 쌓는다
    (한 지원 건의 문서 3종이 한 폴더에 있어야 S7-3 일괄 생성이 자연스럽다). 두 형태가
    실제로 공존할 수 있으므로(과거에 만든 파일이 남아있다) 둘 다 찾되, 폴더는 한 줄로 접는다.
    """
    d = output_dir(user)
    if not d.exists():
        return []

    cases = []
    for sub in sorted(d.iterdir()):
        if not sub.is_dir() or _is_version_dir(sub):
            continue
        files = sorted(sub.glob('*.md'))
        if not files:
            continue
        draft, final, others = _split_case_files(files)
        updated = max(_mtime(f) for f in files)
        cases.append({
            'title': _case_title(sub.name),
            'folder': sub.name,
            'url': f'/case/{quote(user)}/{quote(sub.name)}',
            'has_draft': draft is not None,
            'has_final': final is not None,
            'others': len(others),
            'updated': datetime.fromtimestamp(updated).strftime('%Y-%m-%d'),
            'mtime': updated,
        })

    for f in sorted(d.glob('*.md')):   # 옛 평평한 구조. 파일 하나가 곧 한 건이다
        cases.append({
            'title': _case_title(f.stem),
            'folder': '',
            'url': f'/output/{quote(user)}/{quote(f.name)}',
            'has_draft': True,
            'has_final': False,
            'others': 0,
            'updated': datetime.fromtimestamp(_mtime(f)).strftime('%Y-%m-%d'),
            'mtime': _mtime(f),
        })

    cases.sort(key=lambda c: c['mtime'], reverse=True)
    return cases


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
