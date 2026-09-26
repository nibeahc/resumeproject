"""포트폴리오 목차/초안 생성기 (ROADMAP S7-2). 완성된 디자인 산출물이 아니라
목차 + 각 장 텍스트 초안까지만 만든다 (ROADMAP 이 의도적으로 좁혀둔 범위).

lib/resume.py(S7-1)와 달리 여기는 LLM 을 부른다 — 이력서는 "재배치"라 이미 계산된
매칭 결과를 그대로 옮기면 됐지만, 포트폴리오는 각 장을 실제로 새로 서술해야 해서
(문제/배경 → 내가 한 일 → 결과 → 배운 것 구조로 다듬어 쓰는 것) 결정적 코드로
대체할 수 없는 판단이 들어간다.

프롬프트 크기를 줄이고 "왜 이 프로젝트가 실렸는지"를 투명하게 하려고, 공고가 주어지고
그 공고에 대한 매칭 기록(lib/matcher.py)이 있으면 그 매칭이 이미 골라둔 근거 경험만
후보로 좁힌다(S3-1 lib/selector.py 와 같은 이유) — 전체 경험을 다시 보여주지 않는다.
매칭 기록이 없으면(공고 없이 부르거나 아직 매칭 안 한 공고) 경험 전체를 후보로 준다.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from lib import db
from lib.llm import stream_complete
from lib.skills import load_skill

_TOC_BLOCK = re.compile(r'===TOC===\s*\n(?P<body>.*?)\n===END===', re.S)
_CHAPTER_BLOCK = re.compile(r'===CHAPTER:\s*(?P<num>\d+)===\s*\n(?P<body>.*?)\n===END===', re.S)
_REFERENCE_LINE = re.compile(r'^참조한 경험:\s*(?P<filename>.+?)\s*$', re.M)
_NUMBER = re.compile(r'\d[\d,.]*%?')

MAX_CANDIDATES = 8  # 매칭 기록이 없을 때 전체 경험을 다 보내지 않기 위한 상한


@dataclass
class Chapter:
    number: int
    title: str
    text: str
    evidence_file: Optional[str] = None
    ungrounded_numbers: List[str] = field(default_factory=list)  # 근거 경험에 없는 수치(의심)


@dataclass
class PortfolioResult:
    ok: bool
    path: str = ''
    markdown: str = ''
    toc: List[str] = field(default_factory=list)
    chapters: List[Chapter] = field(default_factory=list)
    candidates: List[str] = field(default_factory=list)
    error: str = ''


def _load_job(job_id: int, db_path=None):
    conn = db.get_conn(db_path)
    try:
        row = conn.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _latest_match(user: str, job_id: int, db_path=None):
    conn = db.get_conn(db_path)
    try:
        row = conn.execute(
            'SELECT * FROM matches WHERE user = ? AND job_id = ? ORDER BY id DESC LIMIT 1',
            (user, job_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _candidate_files(user: str, job_id: Optional[int], exp_dir: Path, db_path=None) -> List[str]:
    """포트폴리오 후보 경험 파일명. 매칭 기록이 있으면 거기서 만족된 조건의 근거만,
    없으면 전체 경험(최근 순, 상한 MAX_CANDIDATES)을 후보로 준다."""
    if job_id is not None:
        match = _latest_match(user, job_id, db_path)
        if match:
            evidence = json.loads(match['evidence_json'])
            seen, ordered = set(), []
            for e in evidence:
                ef = e.get('evidence_file')
                if ef and e['verdict'] != '미충족' and ef not in seen:
                    seen.add(ef)
                    ordered.append(ef)
            if ordered:
                return ordered
    if not exp_dir.exists():
        return []
    files = sorted(exp_dir.glob('*.md'), key=lambda f: f.name, reverse=True)
    return [f.name for f in files[:MAX_CANDIDATES]]


def build_prompt(name: str, job: Optional[dict], candidates: List[tuple], profile_text: str,
                  writing_style_text: str, interest_text: str) -> str:
    exp_block = '\n\n'.join(f'--- experiences/{fn} ---\n{text}' for fn, text in candidates)
    job_block = (
        f"지원 공고: {job['company']} · {job['position']}\n"
        f"필수조건: {json.loads(job['required_json'])}\n우대조건: {json.loads(job['preferred_json'])}\n"
        if job else '공고 없이 일반 포트폴리오 초안을 만듭니다. users/[이름]/관심직무.md 를 참고하세요.\n'
    )
    return f"""[요청]
사용자: {name}
{job_block}
skills/write-portfolio.md 절차대로 아래 후보 경험만 가지고 목차와 장별 텍스트 초안을 만드세요.
후보 이외의 경험은 언급하지 마세요(주어지지 않았으므로 내용을 알 수 없습니다).

--- users/{name}/writing-style.md (어투 참고) ---
{writing_style_text or '(없음)'}

--- users/{name}/관심직무.md ---
{interest_text or '(없음)'}

--- 후보 경험 ({len(candidates)}개) ---
{exp_block}
"""


def parse_llm_output(text: str):
    m_toc = _TOC_BLOCK.search(text)
    if not m_toc:
        return None, None, 'LLM 출력에서 TOC 블록을 찾지 못했습니다 (형식 오류).'
    toc_lines = [line.strip() for line in m_toc.group('body').strip().splitlines() if line.strip()]

    chapters = []
    for m in _CHAPTER_BLOCK.finditer(text):
        num = int(m.group('num'))
        body = m.group('body').strip()
        ref_m = _REFERENCE_LINE.search(body)
        title_line = next((t for t in toc_lines if t.startswith(f'{num}.')), f'{num}.')
        title = title_line.split('.', 1)[1].strip() if '.' in title_line else title_line
        chapters.append(Chapter(
            number=num, title=title, text=body,
            evidence_file=ref_m.group('filename') if ref_m else None,
        ))
    if not chapters:
        return None, None, 'LLM 출력에서 CHAPTER 블록을 찾지 못했습니다 (형식 오류).'
    chapters.sort(key=lambda c: c.number)
    return toc_lines, chapters, None


def _validate_chapters(chapters: List[Chapter], exp_dir: Path, candidate_texts: dict) -> List[Chapter]:
    """1) 인용한 경험 파일이 실재/후보 안에 있는지, 2) 챕터 본문의 숫자가 그 경험 원문에
    실제로 등장하는지(없는 수치를 지어내지 않았는지) 코드로 재확인한다. 못 믿는다 —
    이 프로젝트 전체가 반복해온 원칙(글자수 자체보고, 근거 파일명 등)을 여기도 적용한다."""
    result = []
    for c in chapters:
        if c.evidence_file and c.evidence_file not in candidate_texts:
            c.evidence_file = None  # 후보에 없는(=지어낸) 파일명 인용은 무효화
        if c.evidence_file:
            source_text = candidate_texts[c.evidence_file]
            # "참조한 경험: 2026-파일명.md" 줄 자체에도 숫자가 들어있다(연도 등) — 이건 서술이
            # 아니라 인용 표시이므로 검사 대상에서 뺀다. 안 그러면 파일명의 연도가 "근거 없는
            # 수치"로 오탐된다(실측으로 확인된 문제).
            prose = _REFERENCE_LINE.sub('', c.text)
            body_numbers = set(_NUMBER.findall(prose))
            source_numbers = set(_NUMBER.findall(source_text))
            c.ungrounded_numbers = sorted(n for n in body_numbers if n not in source_numbers)
        result.append(c)
    return result


def _render(name: str, job: Optional[dict], toc: List[str], chapters: List[Chapter]) -> str:
    lines = [f"# {name} 포트폴리오 초안" + (f" — {job['company']} {job['position']}" if job else ''), '']
    lines.append('> 목차와 각 장 텍스트 초안까지입니다. 디자인/레이아웃은 포함하지 않습니다.\n')
    lines.append('## 목차\n')
    lines.extend(toc)
    lines.append('')
    for c in chapters:
        lines.append(f'## {c.number}. {c.title}\n')
        lines.append(c.text)
        if c.ungrounded_numbers:
            lines.append(f'\n> ⚠️ 확인 필요: 이 장의 수치 {c.ungrounded_numbers} 가 참조 경험 원문에서 확인되지 않았습니다.')
        lines.append('')
    return '\n'.join(lines)


def generate_portfolio(user: str, exp_dir: Path, job_id: Optional[int] = None, profile_text: str = '',
                        writing_style_text: str = '', interest_text: str = '', db_path=None,
                        output_dir: Path = None, model=None) -> PortfolioResult:
    job = _load_job(job_id, db_path) if job_id is not None else None
    if job_id is not None and not job:
        return PortfolioResult(ok=False, error=f'공고를 찾을 수 없습니다 (id={job_id}).')

    candidate_names = _candidate_files(user, job_id, exp_dir, db_path)
    if not candidate_names:
        return PortfolioResult(ok=False, error='포트폴리오에 쓸 경험이 없습니다.')

    candidate_texts = {name: (exp_dir / name).read_text(encoding='utf-8') for name in candidate_names}
    candidates = list(candidate_texts.items())

    system = load_skill('write-portfolio')
    user_msg = build_prompt(user, job, candidates, profile_text, writing_style_text, interest_text)
    try:
        result = stream_complete(system=system, messages=[{'role': 'user', 'content': user_msg}],
                                  model=model, max_tokens=4096)
    except Exception as e:  # noqa: BLE001
        return PortfolioResult(ok=False, error=f'LLM 호출 실패: {e}', candidates=candidate_names)

    toc, chapters, err = parse_llm_output(result['text'])
    if err:
        return PortfolioResult(ok=False, error=err, candidates=candidate_names)

    chapters = _validate_chapters(chapters, exp_dir, candidate_texts)
    markdown = _render(user, job, toc, chapters)

    path_str = ''
    if output_dir is not None:
        from lib.resume import _safe_folder_name  # 안전한 폴더명 규칙을 이력서와 통일
        folder = _safe_folder_name(job['company'], job['position']) if job else '포트폴리오'
        target_dir = (output_dir / folder).resolve()
        if not target_dir.is_relative_to(output_dir.resolve()):
            return PortfolioResult(ok=False, error='회사명/직무명에서 안전한 폴더명을 만들 수 없습니다.')
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / 'portfolio-outline.md'
        target.write_text(markdown + '\n', encoding='utf-8')
        path_str = f'{folder}/portfolio-outline.md'

    return PortfolioResult(ok=True, path=path_str, markdown=markdown, toc=toc, chapters=chapters,
                            candidates=candidate_names)
