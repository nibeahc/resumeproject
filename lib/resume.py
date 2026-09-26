"""이력서 생성기 (ROADMAP S7-1). "창작이 아니라 재배치다" — 이미 계산된 매칭 결과
(lib/matcher.py 가 matches 테이블에 남긴, 조건별 근거 경험)를 그대로 재사용해서, 공고
필수조건 순서에 맞춰 경험을 재배열한 이력서를 코드로 만든다. 새로 LLM 을 부르지 않는다
(S3-1 의 lib/selector.py 와 같은 이유 — "이 조건엔 이 경험" 판단은 match_job() 이 이미
끝냈고, 여기서 할 일은 그 판단을 조건 순서대로 재배열하고 문서 형식으로 옮기는 것뿐이다).

매칭 기록이 없는 공고(아직 match_job() 을 부른 적 없음)에는 이 모듈을 쓸 수 없다 —
skills/write-resume.md 를 대화로 직접 실행해 처음부터 판단해야 한다.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from lib import db

_TITLE = re.compile(r'^# (.+)$', re.M)
_RESULT_SECTION = re.compile(r'### Result \(결과\)\s*\n+(.*?)(?=\n#{1,3} |\Z)', re.S)

# 파일시스템 경로(디렉터리명)로 못 쓰는 문자. company/position 은 jobs 테이블 값이라
# (job_parser.commit() 이 DB 용도로만 검증하고 파일명 안전성은 보지 않는다) 여기서
# 직접 한 번 더 걸러낸다 — 그러지 않으면 회사명에 "/" 나 ".." 가 들어왔을 때 의도치
# 않은 경로로 쓸 수 있다.
_UNSAFE_PATH_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


@dataclass
class ResumeResult:
    ok: bool
    path: str = ''
    markdown: str = ''
    order: List[str] = field(default_factory=list)          # evidence_file 등장 순서(중복 제거)
    unmatched_conditions: List[str] = field(default_factory=list)
    error: str = ''


def _safe_folder_name(company: str, position: str) -> str:
    raw = f'{company}_{position}'.strip()
    cleaned = _UNSAFE_PATH_CHARS.sub('', raw).strip().strip('.')
    return cleaned or 'unknown'


def _parse_experience(text: str) -> Dict[str, str]:
    def field_(pattern):
        m = re.search(pattern, text, re.M)
        return m.group(1).strip() if m else ''

    title_m = _TITLE.search(text)
    result_m = _RESULT_SECTION.search(text)
    return {
        'title': title_m.group(1).strip() if title_m else '',
        'period': field_(r'^- 시기: (.*)$'),
        'org': field_(r'^- 기관명: (.*)$'),
        'role': field_(r'^- 역할: (.*)$'),
        'result': result_m.group(1).strip() if result_m else '',
    }


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


def _render(profile_text: str, job: dict, required: List[dict], order: List[str],
            condition_map: Dict[str, List[int]], experiences: Dict[str, dict], other_files: List[str]) -> str:
    lines = [f"# {job['company']} {job['position']} 이력서\n"]
    lines.append(
        '> 이 문서는 창작이 아니라 재배치다. 아래 순서는 이 공고의 필수조건 순서에 맞춰\n'
        '> lib/resume.py 가 matches 테이블의 근거 경험을 그대로 배치한 것이다.\n'
    )

    lines.append('## 기본 정보\n')
    lines.append(profile_text.strip() or '(profile.md 없음)')
    lines.append('')

    lines.append('## 대응하는 필수조건\n')
    for i, cond in enumerate(required, 1):
        lines.append(f"{i}. {cond['condition']}")
    lines.append('')

    lines.append('## 경력 및 활동\n')
    if not order:
        lines.append('(대응하는 경험을 찾지 못했습니다.)')
    for name in order:
        exp = experiences.get(name)
        cond_nums = ', '.join(str(n) for n in condition_map[name])
        if not exp:
            lines.append(f'### {name} — 대응 조건: {cond_nums}\n\n(경험 파일을 찾을 수 없음)\n')
            continue
        lines.append(f"### {exp['title'] or name} — 대응 조건: {cond_nums}\n")
        lines.append(f"- 기간: {exp['period']}")
        lines.append(f"- 기관명 / 역할: {exp['org']} / {exp['role']}")
        lines.append(f"- 핵심 성과: {exp['result'] or '(Result 섹션 없음)'}")
        lines.append('')

    lines.append('## 기타 경험 (이 공고 필수조건과 직접 대응하지 않음)\n')
    if other_files:
        for name in other_files:
            lines.append(f'- {name}')
    else:
        lines.append('(없음)')
    lines.append('')

    lines.append('## 근거\n')
    lines.append('- 이 순서는 이 공고에 대한 매칭 결과(matches 테이블)의 근거 경험을 그대로 따른다.')
    lines.append('- 근거 경험 없이 조건 순서에 배치된 항목은 없다.')

    return '\n'.join(lines)


def generate_resume(user: str, job_id: int, exp_dir: Path, profile_text: str = '',
                     db_path=None, output_dir: Path = None) -> ResumeResult:
    """이미 실행된 매칭 결과(lib.matcher.match_job)를 재사용해 이력서를 만든다.

    output_dir 을 주면 output_dir/[회사명]_[직무명]/이력서.md 로 저장한다(S7-2 의 폴더
    구조를 그대로 따름 — outputs/[이름]/[기업]_[직무]/*.md). 안 주면 markdown 만 돌려준다.
    """
    job = _load_job(job_id, db_path)
    if not job:
        return ResumeResult(ok=False, error=f'공고를 찾을 수 없습니다 (id={job_id}).')

    match = _latest_match(user, job_id, db_path)
    if not match:
        return ResumeResult(ok=False, error='이 공고에 대한 매칭 결과가 없습니다. 먼저 매칭을 실행하세요.')

    evidence = json.loads(match['evidence_json'])
    required = [e for e in evidence if e['kind'] == '필수']

    order: List[str] = []
    condition_map: Dict[str, List[int]] = {}
    unmatched: List[str] = []
    for i, cond in enumerate(required, 1):
        ef = cond.get('evidence_file')
        if cond['verdict'] == '미충족' or not ef:
            unmatched.append(cond['condition'])
            continue
        if ef not in condition_map:
            order.append(ef)
            condition_map[ef] = []
        condition_map[ef].append(i)

    experiences = {}
    for name in order:
        p = exp_dir / name
        if p.exists():
            experiences[name] = _parse_experience(p.read_text(encoding='utf-8'))

    all_files = sorted(f.name for f in exp_dir.glob('*.md')) if exp_dir.exists() else []
    other_files = [f for f in all_files if f not in condition_map]

    markdown = _render(profile_text, job, required, order, condition_map, experiences, other_files)

    path_str = ''
    if output_dir is not None:
        folder = _safe_folder_name(job['company'], job['position'])
        target_dir = (output_dir / folder).resolve()
        if not target_dir.is_relative_to(output_dir.resolve()):
            return ResumeResult(ok=False, error='회사명/직무명에서 안전한 폴더명을 만들 수 없습니다.')
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / '이력서.md'
        target.write_text(markdown + '\n', encoding='utf-8')
        path_str = f'{folder}/이력서.md'  # output_dir 기준 상대 경로. 절대경로는 호출자가 output_dir 를 안다

    return ResumeResult(ok=True, path=path_str, markdown=markdown, order=order, unmatched_conditions=unmatched)
