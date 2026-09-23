"""매칭 엔진 (ROADMAP S5-1). skills/match-jobs.md 를 system 프롬프트로 써서
공고의 필수/우대조건을 사용자 경험과 조건 단위로 대조하고, matches 테이블에 기록한다.

ROADMAP 1-C(환각 방지)가 이 모듈의 핵심 제약이다: "근거 경험 파일명 없이 충족 판정 금지".
스킬 프롬프트로 이 원칙을 지시하는 것만으로는 부족하다는 게 이 프로젝트의 반복된 교훈이라
(S1-1 글자수, S4-2 공고 ID/수집일), 여기서도 모델이 낸 판정을 그대로 믿지 않는다:
  - 인용된 근거 파일명이 실제로 exp_dir 에 존재하는지 코드가 검증한다. 없으면 그 판정을
    "미충족"으로 강제로 낮춘다 (모델이 뭐라고 판정했든 상관없이).
  - "충족"/"부분충족"인데 근거 파일명이 아예 없으면 마찬가지로 "미충족"으로 낮춘다.
  - 필수/우대 충족률과 최종 점수는 이렇게 검증까지 끝난 판정에서 코드가 직접 계산한다
    (모델에게 점수나 충족률을 물어보지 않는다).
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from lib import db
from lib.llm import stream_complete
from lib.skills import load_skill

_EVIDENCE_BLOCK = re.compile(r'===EVIDENCE_JSON===\s*\n(?P<body>.*?)\n===END===', re.S)

VERDICTS = ('충족', '부분충족', '미충족')
_VERDICT_WEIGHT = {'충족': 1.0, '부분충족': 0.5, '미충족': 0.0}

# ROADMAP S5-1 이 못박은 분류 기준. 종합 점수가 아니라 "필수 충족률" 만으로 분류한다.
CATEGORY_THRESHOLDS = (('적합', 0.8), ('도전', 0.5))  # 이 아래는 '부적합'


@dataclass
class ConditionVerdict:
    condition: str
    kind: str                            # '필수' | '우대'
    verdict: str                         # '충족' | '부분충족' | '미충족'
    evidence_file: Optional[str] = None
    note: str = ''
    demoted: bool = False                # 코드가 근거 없음/근거 파일 부재로 강등했는가


@dataclass
class MatchResult:
    ok: bool
    match_id: Optional[int] = None
    job_id: Optional[int] = None
    user: str = ''
    company: str = ''
    position: str = ''
    score: float = 0.0                   # 0~100
    required_hit: float = 0.0            # 0~1
    preferred_hit: float = 0.0           # 0~1
    category: str = ''                   # 적합 | 도전 | 부적합
    evidence: List[ConditionVerdict] = field(default_factory=list)
    missing: List[dict] = field(default_factory=list)
    error: str = ''


def _load_job(job_id: int, db_path=None):
    conn = db.get_conn(db_path)
    try:
        row = conn.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _load_experiences(exp_dir: Path):
    """(파일명, 전문) 목록. 파일명 오름차순 — 프롬프트와 검증 양쪽에서 순서가 같아야 재현 가능하다."""
    if not exp_dir.exists():
        return []
    return [(f.name, f.read_text(encoding='utf-8')) for f in sorted(exp_dir.glob('*.md'))]


def build_prompt(company: str, position: str, required: List[str], preferred: List[str],
                  experiences: List[tuple]) -> str:
    cond_lines = [f'{i+1}. [필수] {c}' for i, c in enumerate(required)]
    cond_lines += [f'{len(required)+i+1}. [우대] {c}' for i, c in enumerate(preferred)]
    cond_block = '\n'.join(cond_lines) if cond_lines else '(조건 없음)'

    if experiences:
        exp_block = '\n\n'.join(f'--- experiences/{name} ---\n{text}' for name, text in experiences)
    else:
        exp_block = '(경험 파일이 없습니다 — 모든 조건을 미충족으로 판정하세요)'

    return f"""[요청]
공고: {company} · {position}

아래 조건 목록을 skills/match-jobs.md 절차대로 이 사용자의 경험과 대조하세요.
EVIDENCE_JSON 배열의 조건 순서와 개수는 아래 목록과 정확히 같아야 합니다(빠뜨리거나
더하지 마세요). evidence_file 은 아래 경험 목록의 파일명 중 하나이거나, 근거가 없으면
null 이어야 합니다 (목록에 없는 파일명을 지어내면 안 됩니다).

--- 대조할 조건 (필수 {len(required)}개, 우대 {len(preferred)}개) ---
{cond_block}

--- 사용자 경험 전문 ({len(experiences)}개) ---
{exp_block}
"""


def parse_llm_output(text: str, required: List[str], preferred: List[str]):
    """블록을 파싱하고 개수/순서를 검증한다. 실패 사유를 문자열로 돌려준다 (성공 시 None)."""
    m = _EVIDENCE_BLOCK.search(text)
    if not m:
        return None, 'LLM 출력에서 EVIDENCE_JSON 블록을 찾지 못했습니다 (형식 오류).'
    try:
        items = json.loads(m.group('body'))
    except json.JSONDecodeError as e:
        return None, f'JSON 파싱 실패: {e}'
    if not isinstance(items, list):
        return None, 'EVIDENCE_JSON 이 배열이 아닙니다.'

    expected = [('필수', c) for c in required] + [('우대', c) for c in preferred]
    if len(items) != len(expected):
        return None, f'조건 개수가 다릅니다 (기대 {len(expected)}, 실제 {len(items)}).'

    verdicts = []
    for item, (exp_kind, exp_condition) in zip(items, expected):
        if not isinstance(item, dict):
            return None, '조건 판정 하나가 객체가 아닙니다.'
        verdict = item.get('verdict')
        if verdict not in VERDICTS:
            return None, f'알 수 없는 판정값: {verdict!r}'
        verdicts.append(ConditionVerdict(
            condition=exp_condition, kind=exp_kind, verdict=verdict,
            evidence_file=item.get('evidence_file') or None, note=(item.get('note') or '').strip(),
        ))
    return verdicts, None


def _validate_evidence(verdicts: List[ConditionVerdict], exp_dir: Path) -> List[ConditionVerdict]:
    """1-C 를 코드로 강제한다: 근거 파일이 실재하지 않거나 아예 없는데 충족/부분충족이면 미충족으로 낮춘다."""
    existing = {f.name for f in exp_dir.glob('*.md')} if exp_dir.exists() else set()
    result = []
    for v in verdicts:
        if v.verdict != '미충족' and (not v.evidence_file or v.evidence_file not in existing):
            reason = '근거 경험 파일명이 없음' if not v.evidence_file else f'인용된 파일이 실재하지 않음: {v.evidence_file}'
            result.append(ConditionVerdict(
                condition=v.condition, kind=v.kind, verdict='미충족', evidence_file=None,
                note=f'{reason} (자동 강등, 원래 판정: {v.verdict})', demoted=True,
            ))
        else:
            result.append(v)
    return result


def _score(verdicts: List[ConditionVerdict]):
    required = [v for v in verdicts if v.kind == '필수']
    preferred = [v for v in verdicts if v.kind == '우대']
    required_hit = (sum(_VERDICT_WEIGHT[v.verdict] for v in required) / len(required)) if required else 1.0
    preferred_hit = (sum(_VERDICT_WEIGHT[v.verdict] for v in preferred) / len(preferred)) if preferred else 0.0
    score = (required_hit * 0.7 + preferred_hit * 0.3) * 100
    category = '부적합'
    for name, threshold in CATEGORY_THRESHOLDS:
        if required_hit >= threshold:
            category = name
            break
    return required_hit, preferred_hit, score, category


def match_job(user: str, job_id: int, exp_dir: Path, db_path=None, model=None) -> MatchResult:
    """공고 하나를 사용자 경험과 매칭해 matches 테이블에 한 행을 추가한다.

    같은 (user, job_id) 조합을 여러 번 매칭해도 매번 새 행이 쌓인다 — 경험이 늘거나 공고가
    바뀌면 점수가 달라질 수 있으므로, matches 는 이력으로 남긴다 (스키마에 UNIQUE 제약 없음).
    """
    job = _load_job(job_id, db_path)
    if not job:
        return MatchResult(ok=False, job_id=job_id, user=user, error=f'공고를 찾을 수 없습니다 (id={job_id}).')

    required = json.loads(job['required_json'])
    preferred = json.loads(job['preferred_json'])
    experiences = _load_experiences(exp_dir)

    system = load_skill('match-jobs')
    user_msg = build_prompt(job['company'], job['position'], required, preferred, experiences)
    try:
        result = stream_complete(system=system, messages=[{'role': 'user', 'content': user_msg}],
                                  model=model, max_tokens=4096)
    except Exception as e:  # noqa: BLE001
        return MatchResult(ok=False, job_id=job_id, user=user, company=job['company'],
                            position=job['position'], error=f'LLM 호출 실패: {e}')

    verdicts, err = parse_llm_output(result['text'], required, preferred)
    if err:
        return MatchResult(ok=False, job_id=job_id, user=user, company=job['company'],
                            position=job['position'], error=err)

    verdicts = _validate_evidence(verdicts, exp_dir)
    required_hit, preferred_hit, score, category = _score(verdicts)
    missing = [{'condition': v.condition, 'kind': v.kind, 'note': v.note}
               for v in verdicts if v.verdict != '충족']

    evidence_json = [
        {'condition': v.condition, 'kind': v.kind, 'verdict': v.verdict,
         'evidence_file': v.evidence_file, 'note': v.note}
        for v in verdicts
    ]

    conn = db.get_conn(db_path)
    try:
        conn.execute(
            'INSERT INTO matches (user, job_id, score, required_hit, preferred_hit, evidence_json, missing_json) '
            'VALUES (?,?,?,?,?,?,?)',
            (user, job_id, score, required_hit, preferred_hit,
             json.dumps(evidence_json, ensure_ascii=False), json.dumps(missing, ensure_ascii=False)),
        )
        match_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        conn.commit()
    finally:
        conn.close()

    return MatchResult(
        ok=True, match_id=match_id, job_id=job_id, user=user, company=job['company'], position=job['position'],
        score=score, required_hit=required_hit, preferred_hit=preferred_hit, category=category,
        evidence=verdicts, missing=missing,
    )
