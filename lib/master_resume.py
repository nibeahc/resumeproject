"""마스터 이력서 (S11-24).

**기업 맞춤 자소서를 쓰기 전에 가장 먼저 만드는 문서다.** 모든 경력, 교육, 프로젝트,
성과를 한곳에 모아 두고, 지원할 때마다 공고에 맞게 발췌해서 쓴다.

이 레포에는 재료가 이미 흩어져 있다 — `profile.md` 에 인적사항과 강점, `experiences/`
에 STAR 로 정리된 경험 23건. 마스터 이력서는 **새로 쓰는 문서가 아니라 그것을 한 장으로
모은 것**이다. 그래서 조립은 코드가 하고, 모델은 핵심 역량 요약만 쓴다.

왜 코드가 조립하나 — 마스터 이력서는 누락이 없어야 쓸모가 있다. 모델에게 23건을 주고
"정리해줘" 하면 몇 건은 조용히 빠진다. 빠졌다는 사실 자체를 알 수 없는 것이 문제다.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from lib.selector import Experience, load_all_experiences, rank_experiences

# 역할과 제목으로 가른다. 한 경험이 여러 칸에 걸칠 수 있으므로 **가장 강한 신호 하나**만 본다.
SECTION_EXPERIENCE = 'experience'   # 경력 및 업무 경험
SECTION_PROJECT = 'project'         # 프로젝트 및 성과
SECTION_EDUCATION = 'education'     # 학력 및 자격/교육
SECTION_ACTIVITY = 'activity'       # 대외활동

SECTION_TITLES = {
    SECTION_EXPERIENCE: '경력 및 업무 경험',
    SECTION_PROJECT: '프로젝트 및 성과',
    SECTION_ACTIVITY: '대외활동',
    SECTION_EDUCATION: '교육 및 학습',
}
SECTION_ORDER = (SECTION_EXPERIENCE, SECTION_PROJECT, SECTION_ACTIVITY, SECTION_EDUCATION)

_WORK = re.compile(r'인턴|근로장학생|아르바이트|사원|연구원|조교|보조')
_PROJECT = re.compile(r'공모전|경진대회|해커톤|프로젝트|연구|학술용역|개발|분석')
_EDUCATION = re.compile(r'특강|교육|스터디|소모임|수강생|교환학생|연수')
_ACTIVITY = re.compile(r'봉사|동아리|학생회|단원|버디|멘토')

_NUMBER = re.compile(r'\d')
# 숫자만 성과인 것은 아니다. 수상, 논문 등재, 채택, 체결은 숫자가 없어도 **외부가 확인한
# 구체적 결과**다. 처음엔 숫자만 봤더니 "최우수상(중부발전사장상) 수상" 과 "MOU 체결" 이
# 정량 성과 없음으로 잡혔다 — 검사 이름이 틀렸던 것이다 (2026-10-04 실사용 중 발견).
_AWARD = re.compile(r'(수상|대상|최우수상|우수상|장상|입선|등재|게재|채택|선정|체결|'
                    r'특허|출원|합격|정규 ?프로그램)')
# 이력서 작성법: "열심히 했다 / 많이 배웠다 / 느낀 점이 많다" 는 일을 잘한다는 증거가 아니다.
VAGUE_REFLECTION = re.compile(
    r'(열심히|최선을 다|많이 배웠|배울 수 있었|느낀 점|보람|뜻깊|값진 경험|성장할 수 있었|'
    r'좋은 경험이었|소중한 경험)')


def classify(exp: Experience, role: str = '') -> str:
    """경험을 이력서의 어느 칸에 넣을지 정한다. 역할이 1순위, 제목이 2순위다."""
    hay = f'{role} {exp.title} {exp.filename}'
    if _WORK.search(role or ''):
        return SECTION_EXPERIENCE
    if _ACTIVITY.search(hay) and not _PROJECT.search(hay):
        return SECTION_ACTIVITY
    if _PROJECT.search(hay):
        return SECTION_PROJECT
    if _EDUCATION.search(hay):
        return SECTION_EDUCATION
    if _WORK.search(hay):
        return SECTION_EXPERIENCE
    return SECTION_ACTIVITY


@dataclass
class Entry:
    filename: str
    title: str
    period: str = ''
    org: str = ''
    role: str = ''
    keywords: List[str] = field(default_factory=list)
    result: str = ''
    section: str = SECTION_ACTIVITY
    # 숫자 또는 수상·등재·채택처럼 외부가 확인한 결과가 Result 에 있는가.
    has_outcome: bool = False
    # 메타에 "- 정량성과: 해당없음" 이 있으면 보강 권고를 끈다 (사용자 판단).
    number_waived: bool = False
    vague: List[str] = field(default_factory=list)


@dataclass
class MasterResume:
    profile_lines: List[str] = field(default_factory=list)
    entries: List[Entry] = field(default_factory=list)

    def by_section(self, section: str) -> List[Entry]:
        return [e for e in self.entries if e.section == section]


_RESULT_BLOCK = re.compile(r'^### Result[^\n]*\n(?P<body>.*?)(?=^## |^### |\Z)', re.S | re.M)
_PROFILE_FIELDS = ('이름', '학교/소속', '연락처', '이메일', '생년월일', 'GPA', '목표 직무')


_META_LINE = re.compile(r'^- (시기|기관명|역할|정량성과): (.+)$', re.M)
# 모든 경험에 숫자가 있어야 하는 것은 아니다. 사용자가 "이 경험은 수행 사실 자체가
# 요점" 이라고 정하면 경고를 끈다 — 지울 수 없는 경고는 노이즈가 된다.
_NUMBER_WAIVED = re.compile(r'(해당\s*없음|불필요|필요\s*없음|없음)')


def _meta(path: Path) -> dict:
    """경험 파일 메타에서 시기, 기관명, 역할을 읽는다.

    lib/selector.Experience 는 이 셋을 들고 있지 않다 — 선택에 쓰지 않기 때문이다.
    이력서에는 필요하므로 여기서 따로 읽는다 (selector 의 책임을 늘리지 않는다).
    """
    text = path.read_text(encoding='utf-8')
    got = {k: v.strip() for k, v in _META_LINE.findall(text)}
    return {'period': got.get('시기', ''), 'org': got.get('기관명', ''),
            'role': got.get('역할', ''),
            'number_waived': bool(_NUMBER_WAIVED.search(got.get('정량성과', '')))}


def _result_text(path: Path) -> str:
    m = _RESULT_BLOCK.search(path.read_text(encoding='utf-8'))
    if not m:
        return ''
    body = re.sub(r'^\s*[-*]\s*\[.\]\s.*$', '', m.group('body'), flags=re.M)
    return re.sub(r'\n{2,}', '\n', body).strip()


def collect(user_root: Path, exp_dir: Path) -> MasterResume:
    """profile.md 와 experiences/ 를 읽어 마스터 이력서 재료를 모은다. 판단은 하지 않는다."""
    profile_lines = []
    profile_path = user_root / 'profile.md'
    if profile_path.exists():
        for line in profile_path.read_text(encoding='utf-8').split('\n'):
            s = line.strip()
            if s.startswith('- ') and any(f in s for f in _PROFILE_FIELDS):
                profile_lines.append(s[2:].strip())
            elif s.startswith('- ') and ('TOEIC' in s or 'OPIc' in s or '자격' in s):
                profile_lines.append(s[2:].strip())

    entries = []
    for exp in load_all_experiences(exp_dir):
        path = exp_dir / exp.filename
        result = _result_text(path)
        meta = _meta(path)
        entries.append(Entry(
            filename=exp.filename, title=exp.title, period=meta['period'], org=meta['org'],
            role=meta['role'], keywords=list(exp.keywords), result=result,
            section=classify(exp, meta['role']),
            has_outcome=bool(_NUMBER.search(result) or _AWARD.search(result)),
            number_waived=meta['number_waived'],
            vague=sorted(set(VAGUE_REFLECTION.findall(result))),
        ))
    entries.sort(key=lambda e: e.period, reverse=True)
    return MasterResume(profile_lines=profile_lines, entries=entries)


def lint(resume: MasterResume) -> List[str]:
    """마스터 이력서가 쓸 만한 상태인지 본다. 막지 않고 알린다."""
    issues = []
    if not resume.profile_lines:
        issues.append('profile_empty')
    if not resume.entries:
        issues.append('no_entries')
    if not resume.by_section(SECTION_EXPERIENCE):
        issues.append('no_work_experience')
    # 이력서 작성법: 성과 중심으로 쓴다. 숫자든 수상이든 **외부가 확인한 결과**가
    # 없는 항목을 알려 보강하게 한다.
    if needs_numbers(resume):
        issues.append('result_without_outcome')
    if any(e.vague for e in resume.entries):
        issues.append('vague_reflection')
    return issues


def needs_numbers(resume: MasterResume) -> List[str]:
    """구체적 결과가 안 보이는 항목. 사용자가 "해당없음" 으로 정한 것은 빼고 센다."""
    return [e.filename for e in resume.entries
            if not e.has_outcome and not e.number_waived]


def vague_entries(resume: MasterResume) -> List[dict]:
    return [{'filename': e.filename, 'words': e.vague} for e in resume.entries if e.vague]


def customize(resume: MasterResume, exp_dir: Path, job_text: str,
              question: str = '', interest_text: str = '', top: int = 8) -> List[dict]:
    """공고에 맞춰 발췌한다 (S11-20 의 공고 반영 순위를 그대로 쓴다).

    **경험 갯수가 아니라 직무와의 연결이 기준이다.** 화려해도 연관이 낮으면 뺀다.
    """
    _, ranked = rank_experiences(question or job_text, load_all_experiences(exp_dir),
                                  interest_text=interest_text, job_text=job_text)
    by_name = {e.filename: e for e in resume.entries}
    out = []
    for c in ranked[:top]:
        e = by_name.get(c.experience.filename)
        if e is None:
            continue
        out.append({'filename': e.filename, 'title': e.title, 'section': e.section,
                    'score': c.score, 'jd_matched': c.jd_matched, 'reasons': c.reasons})
    return out


def build_markdown(resume: MasterResume, summary: str = '',
                   keywords: Optional[List[str]] = None) -> str:
    """마스터 이력서 Markdown. 누락 없이 전부 싣는다 — 발췌는 지원할 때 한다."""
    out = ['# 마스터 이력서', '',
           '> 모든 경력과 프로젝트, 교육을 한곳에 모은 문서입니다. **제출용이 아닙니다.**',
           '> 지원할 때 공고에 맞는 항목만 발췌해서 쓰세요.',
           '> `lib/master_resume.py` 가 `profile.md` 와 `experiences/` 에서 조립합니다.', '']
    if resume.profile_lines:
        out += ['## 기본 정보', ''] + [f'- {l}' for l in resume.profile_lines] + ['']
    if keywords:
        out += ['## 핵심 역량 키워드', '', ' · '.join(keywords).replace(' · ', ', '), '']
    if summary:
        out += ['## 핵심 역량 요약', '', summary.strip(), '']

    for section in SECTION_ORDER:
        rows = resume.by_section(section)
        if not rows:
            continue
        out += [f'## {SECTION_TITLES[section]}', '']
        for e in rows:
            head = f'### {e.title}'
            out.append(head)
            meta = [x for x in (e.period, e.org, e.role) if x]
            if meta:
                out.append(' | '.join(meta))
            if e.keywords:
                out.append(f'키워드: {", ".join(e.keywords)}')
            if e.result:
                out += ['', e.result]
            flags = []
            if not e.has_outcome and not e.number_waived:
                flags.append('구체적 결과가 안 보임')
            if e.vague:
                flags.append(f'모호한 표현: {", ".join(e.vague)}')
            if flags:
                out.append(f'\n> ⚠️ {" · ".join(flags)} — 보강하면 지원서에서 바로 쓸 수 있습니다')
            out += [f'\n출처: `{e.filename}`', '']
    return '\n'.join(out)


_KEYWORDS_BLOCK = re.compile(r'===KEYWORDS===\n(?P<body>.*?)\n===END===', re.S)
_SUMMARY_BLOCK = re.compile(r'===SUMMARY===\n(?P<body>.*?)\n===END===', re.S)


def build_summary_prompt(resume: MasterResume) -> str:
    """요약에 쓸 재료만 넘긴다. 전문을 다 넣으면 모델이 거기서 새 사실을 만들기 쉽다."""
    lines = []
    for section in SECTION_ORDER:
        rows = resume.by_section(section)
        if not rows:
            continue
        lines.append(f'[{SECTION_TITLES[section]}]')
        for e in rows:
            meta = ' | '.join(x for x in (e.period, e.org, e.role) if x)
            lines.append(f'- {e.title} ({meta})')
            if e.keywords:
                lines.append(f'  키워드: {", ".join(e.keywords)}')
            if e.result:
                lines.append(f'  결과: {e.result.splitlines()[0][:120]}')
    head = '[기본 정보]\n' + '\n'.join(f'- {l}' for l in resume.profile_lines) if resume.profile_lines else ''
    return '\n'.join([head, '', '\n'.join(lines), '',
                      '위 항목만 보고 브랜딩 키워드와 핵심 역량 요약을 쓰세요. '
                      '여기 없는 경력과 수치를 만들지 마세요.']).strip()


def parse_summary(text: str) -> tuple:
    """(키워드 목록, 요약 본문). 형식을 안 지켜도 예외를 내지 않는다."""
    k = _KEYWORDS_BLOCK.search(text or '')
    s = _SUMMARY_BLOCK.search(text or '')
    keywords = [w.strip() for w in re.split(r'[,·]', k.group('body'))] if k else []
    return [w for w in keywords if w], (s.group('body').strip() if s else '')


def lint_summary(keywords: List[str], summary: str) -> List[str]:
    """요약이 이력서의 글인지 본다. 감상문이면 알린다."""
    issues = []
    if not keywords:
        issues.append('summary_no_keywords')
    elif not (2 <= len(keywords) <= 3):
        issues.append('summary_keyword_count')
    if not summary:
        issues.append('summary_missing')
    else:
        n = len([l for l in summary.split('\n') if l.strip()])
        if not (3 <= n <= 5):
            issues.append('summary_line_count')
        if VAGUE_REFLECTION.search(summary):
            issues.append('summary_vague')
    return issues


MASTER_FILENAME = '마스터이력서.md'


def build_master_resume_events(user: str, user_root: Path, exp_dir: Path, *,
                                db_path=None, surface: str = 'script',
                                write: bool = True, complete=None):
    """마스터 이력서를 만들고 (event, data) 를 yield 한다 (S11-24).

    조립은 코드가, 핵심 역량 요약만 모델이 쓴다. LLM 호출 **1회**.
    write=False 면 파일을 쓰지 않고 본문만 돌려준다 (미리보기, 테스트).
    """
    from lib import runs
    from lib.llm import stream_complete
    from lib.skills import load_skill
    complete = complete or stream_complete

    resume = collect(user_root, exp_dir)
    issues = lint(resume)
    yield 'progress', {'stage': 'collected', 'entries': len(resume.entries),
                       'sections': {s: len(resume.by_section(s)) for s in SECTION_ORDER},
                       'needs_numbers': needs_numbers(resume)}
    if not resume.entries:
        yield 'error', {'message': '경험 파일이 없습니다. 먼저 파싱해 주세요.',
                        'issues': issues}
        return

    keywords, summary = [], ''
    try:
        result = complete(system=load_skill('build-master-resume'),
                          messages=[{'role': 'user',
                                      'content': build_summary_prompt(resume)}],
                          max_tokens=1500, on_delta=None)
    except Exception as e:  # noqa: BLE001
        # 요약을 못 받아도 **항목 조립본은 쓸모가 있다.** 요약만 비우고 계속한다.
        issues.append('summary_call_failed')
        yield 'progress', {'stage': 'summary_failed', 'error': str(e)}
    else:
        keywords, summary = parse_summary(result['text'])
        issues.extend(lint_summary(keywords, summary))

    markdown = build_markdown(resume, summary=summary, keywords=keywords)
    path = user_root / MASTER_FILENAME
    if write:
        path.write_text(markdown, encoding='utf-8')

    run_id = runs.new_run_id()
    try:
        runs.record(runs.RunRecord(
            run_id=run_id, user=user, surface=surface, workflow='master_resume.build',
            content_hash=runs.content_hash(markdown),
            status='review_required' if issues else 'ready_for_user',
            issues=list(issues), quality_check=False, reviewed=False,
            inputs={'entries': len(resume.entries),
                    'needs_numbers': needs_numbers(resume)},
        ), db_path=db_path)
    except Exception as e:  # noqa: BLE001
        yield 'progress', {'stage': 'run_record_failed', 'error': str(e)}

    yield 'done', {
        'markdown': markdown,
        'keywords': keywords,
        'summary': summary,
        'issues': issues,
        'status': 'review_required' if issues else 'ready_for_user',
        'entries': len(resume.entries),
        'sections': {s: len(resume.by_section(s)) for s in SECTION_ORDER},
        'needs_numbers': needs_numbers(resume),
        'vague': vague_entries(resume),
        'saved_to': f'users/{user}/{MASTER_FILENAME}' if write else None,
        'run_id': run_id,
    }
