"""자소서 생성 엔진 (ROADMAP S11-7 에서 web/routes/generate.py 에서 분리).

**가장 중요한 규칙 하나: 글자수는 LLM 이 아니라 코드가 잰다.** S1-1 에서 모델이 자체
보고한 글자수(683자)가 실제(715자)와 32자 어긋나는 것을 실측으로 확인했다(PROGRESS.md).
그래서 모델이 뭐라고 보고하든 무시하고 len() 으로 직접 센다.

원본 스킬(skills/write-coverletter.md)의 Step 9 출력 형식은 사람이 읽기엔 좋지만 코드가
안정적으로 파싱하기엔 약하다. 그래서 우리가 정한 구분자 안에 본문만 담아 달라고 요청한다
(스킬 파일 자체는 건드리지 않는다 — ROADMAP 1-B. 구분자는 사용자 메시지에서만 요구한다).

이 모듈은 Flask 를 import 하지 않는다. 경로는 호출자가 web.paths 로 검증해서 Path 로 넘긴다
— lib/resume.py, lib/portfolio.py 와 같은 규약이다.
"""
import queue
import re
import threading
from dataclasses import dataclass, field, replace as dataclass_replace
from datetime import date
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

from lib import trends as trends_module
from lib import versions
from lib.llm import stream_complete
from lib.resume import _safe_folder_name
from lib import runs
from lib.selector import MAX_EXPERIENCES
from lib.selector import select as select_experiences
from lib.skills import ContractError, load_frontmatter, load_skill


_PLAN_BLOCK = re.compile(r'===PLAN===\n(?P<body>.*?)\n===END===', re.S)
_REVIEW_BLOCK = re.compile(r'===REVIEW===\n(?P<body>.*?)\n===END===', re.S)
_ANSWER_BLOCK = re.compile(r'===ANSWER===\n(?P<body>.*?)\n===END===', re.S)
_META_BLOCK = re.compile(r'===META===\n(?P<body>.*?)\n===END===', re.S)
_EVIDENCE_BLOCK = re.compile(r'===EVIDENCE===\n(?P<body>.*?)\n===END===', re.S)

# ---------------------------------------------------------------------------
# 정책 (S11-15, 설계검토 A-3)
#
# 숫자와 필수조건의 **원본은 skills/write-coverletter.md 의 frontmatter** 다.
# 예전에는 같은 값이 코드와 절차서에 각각 적혀 있었고, 한쪽만 고치면 조용히 어긋났다
# (실제로 "최대 2개" 가 스킬·selector·UI 세 곳에 흩어져 있었다).
#
# 계약을 못 읽으면 기본값으로 넘어가지 않고 **import 가 실패한다.** 기본값으로 돌아가면
# 스킬 파일에 적힌 정책이 지켜지는 줄 알게 되는데, 그게 검토가 경고한 상황이다.
# ---------------------------------------------------------------------------

SUPPORTED_CONTRACT_VERSION = 1

# 허용 필드와 타입. 여기 없는 키가 계약에 있으면 오류다 — 오타난 정책이 조용히
# 무시되는 것을 막는다.
_CONTRACT_SCHEMA = {
    'contract_version': int,
    'workflow': str,
    'preconditions': {'company_evidence': str},
    'experience_policy': {'default_count': int, 'max_count': int,
                           'second_experience_requires_shared_claim': bool},
    'evidence_policy': {'require_quote': bool, 'max_quote_chars': int},
    'company_evidence_policy': {'max_age_days': int, 'require_source_url': bool},
    'length_policy': {'max_chars': int, 'tolerance_pct': int},
    'retry_policy': {'max_length_retries': int, 'max_repair_rounds': int},
    'revision_policy': {'return_full_document': bool},
}


def validate_contract(data: dict) -> dict:
    """허용 필드와 타입을 검사한다. 알 수 없는 키나 지원하지 않는 버전은 오류."""
    version = data.get('contract_version')
    if version != SUPPORTED_CONTRACT_VERSION:
        raise ContractError(
            f'지원하지 않는 contract_version: {version!r} '
            f'(이 코드는 {SUPPORTED_CONTRACT_VERSION} 만 압니다)')
    for key, value in data.items():
        if key not in _CONTRACT_SCHEMA:
            raise ContractError(f'알 수 없는 계약 항목: {key!r}')
        expected = _CONTRACT_SCHEMA[key]
        if isinstance(expected, dict):
            if not isinstance(value, dict):
                raise ContractError(f'{key}: 하위 항목이 있어야 합니다')
            for sub_key, sub_value in value.items():
                if sub_key not in expected:
                    raise ContractError(f'알 수 없는 계약 항목: {key}.{sub_key}')
                if not isinstance(sub_value, expected[sub_key]) or isinstance(sub_value, bool) != (
                        expected[sub_key] is bool):
                    raise ContractError(
                        f'{key}.{sub_key}: {expected[sub_key].__name__} 이어야 합니다 (받은 값 {sub_value!r})')
        elif not isinstance(value, expected) or isinstance(value, bool) != (expected is bool):
            raise ContractError(f'{key}: {expected.__name__} 이어야 합니다 (받은 값 {value!r})')
    return data


def load_policy(skill: str = 'write-coverletter') -> dict:
    return validate_contract(load_frontmatter(skill))


POLICY = load_policy()

# 아래 이름들은 계약에서 파생된다. 호출부가 계속 쓸 수 있게 모듈 상수로 노출하되,
# 값을 바꾸려면 skills/write-coverletter.md 를 고친다.
MAX_RETRIES = POLICY['retry_policy']['max_length_retries']
MAX_REPAIRS = POLICY['retry_policy']['max_repair_rounds']
MAX_CHAR_LIMIT = POLICY['length_policy']['max_chars']
LENGTH_TOLERANCE = POLICY['length_policy']['tolerance_pct'] / 100
COMPANY_EVIDENCE_MAX_AGE_DAYS = POLICY['company_evidence_policy']['max_age_days']
COMPANY_EVIDENCE_REQUIRED = POLICY['preconditions']['company_evidence'] == 'required'
MAX_EXPERIENCES_BY_CONTRACT = POLICY['experience_policy']['max_count']
EVIDENCE_REQUIRED = POLICY['evidence_policy']['require_quote']
MAX_QUOTE_CHARS = POLICY['evidence_policy']['max_quote_chars']

# 계약에 넣지 않은 값: 구현 세부사항이라 정책이 아니다.
TARGET_UNDERSHOOT = 20    # 재시도 목표를 상한에 붙이지 않고 이만큼 아래로 둔다 (미세 오차 대비)

# S11-4: "저장했다" 와 "제출해도 된다" 를 분리한다. 이 둘을 같은 값으로 묶어두면
# (예전의 done → coverletter_ok=True) 글자수를 초과한 초안도 성공으로 보고된다.
# 저장은 그대로 한다 — 사람이 손봐서 쓸 수 있으니 버리지 않는다. 다만 ready 로 올리지 않는다.
STATUS_READY = 'ready_for_user'          # 자동 검사를 통과했다. 사용자 최종 승인이나 제출과는 다른 뜻이다
STATUS_REVIEW_REQUIRED = 'review_required'  # 저장은 됐지만 사람이 봐야 한다

ISSUE_OVER_LIMIT = 'over_limit'
ISSUE_UNDERSHOOT = 'undershoot'
ISSUE_DROPPED_OVERRIDES = 'dropped_overrides'
ISSUE_UNRESEARCHED_COMPANY = 'unresearched_company'
ISSUE_CITED_UNKNOWN = 'cited_unknown_experience'
# S11-10 (설계검토 B)
ISSUE_PLAN_MISSING = 'plan_missing'
ISSUE_PLAN_UNKNOWN_EXPERIENCE = 'plan_cites_unknown_experience'
ISSUE_SECOND_EXPERIENCE_UNJUSTIFIED = 'second_experience_unjustified'
ISSUE_REVIEW_FAILED = 'review_failed'
ISSUE_UNSUPPORTED_CLAIM = 'unsupported_claim'
ISSUE_NEW_UNRELATED_CLAIM = 'new_unrelated_claim'
ISSUE_FABRICATED_FACT = 'fabricated_fact'
# S11-11 (설계검토 A-1, A-4, B-2): "코드로 강제하라" 고 한 것들 중 프롬프트에만 맡겨져 있던 것
ISSUE_COMPANY_STALE = 'company_evidence_stale'
ISSUE_COMPANY_THIN = 'company_evidence_thin'
ISSUE_UNKNOWN_NUMBER = 'unverified_number'
ISSUE_UNKNOWN_ORG = 'unverified_organization'
ISSUE_FORBIDDEN_CHAR = 'forbidden_character'
ISSUE_DUPLICATE_OVERRIDE = 'duplicate_override'
ISSUE_UNEXPLAINED_NAME = 'unexplained_name'   # S11-12
ISSUE_SECTION_NOT_FOUND = 'section_not_found'  # S11-13
ISSUE_PLAN_MISSING_WARRANT = 'plan_missing_warrant'  # S11-14
# S11-18: 출처별 증명 범위. 공고에 적혀 있다는 것은 직무가 그것을 요구한다는 뜻이지
# 지원자가 그 일을 했다는 뜻이 아니다 (2차 설계검토 3-1).
ISSUE_FACT_FROM_JD_ONLY = 'fact_from_jd_only'
ISSUE_JD_TERM_UNSUPPORTED = 'jd_term_unsupported'
# S11-19: 근거 구간. 주장마다 경험 원문의 인용을 받아 코드가 대조한다
# (2차 설계검토 3-2).
ISSUE_EVIDENCE_MISSING = 'evidence_missing'
ISSUE_EVIDENCE_QUOTE_NOT_FOUND = 'evidence_quote_not_found'
ISSUE_EVIDENCE_UNKNOWN_FILE = 'evidence_unknown_file'
ISSUE_EVIDENCE_BAD_SENTENCE = 'evidence_bad_sentence'
ISSUE_EVIDENCE_UNCOVERED = 'evidence_uncovered_claim'



# 검토 결과 중 "사람이 반드시 봐야 하는" 유형. 나머지는 기록만 한다.
# skills/review-coverletter.md 의 issues[].kind 와 같은 문자열이어야 한다.
BLOCKING_REVIEW_KINDS = frozenset({ISSUE_UNSUPPORTED_CLAIM, ISSUE_NEW_UNRELATED_CLAIM,
                                    ISSUE_FABRICATED_FACT})



class LengthSpec(NamedTuple):
    """분량 요구사항 (S11-3).

    예전에는 char_limit 양의 정수 하나뿐이라 "제한 없음" 을 표현할 방법이 없었다.
    사용자가 제한을 안 주면 에러였고, 대화 경로에서 "제한 없음" 이라고 말해도 웹에는
    그 상태가 존재하지 않았다 (검토 0.4).

    mode='unlimited' 면 hard_max_chars 는 None 이고 글자수 재시도 루프를 돌지 않는다.
    검토 C-3 의 soft_target_chars / detail_level 은 넣지 않았다 — 입력 UI 가 없으면
    죽은 파라미터가 된다. 대신 "제한 없음 = 깊게 쓰기" 라는 뜻을 프롬프트 지시문에 담는다.
    """
    mode: str                      # 'bounded' | 'unlimited'
    hard_max_chars: Optional[int]  # unlimited 면 None
    count_mode: str                # '포함' | '제외'

    @property
    def bounded(self) -> bool:
        return self.mode == 'bounded'

    @property
    def count_desc(self) -> str:
        return '공백 포함' if self.count_mode == '포함' else '공백 제외'


def count_chars(text, count_mode):
    return len(text) if count_mode == '포함' else len(re.sub(r'\s', '', text))


def read_if_exists(path: Path):
    return path.read_text(encoding='utf-8') if path.exists() else ''


# S11-5: writing-rules.md 의 "(예: ...)" 안내 줄을 실제 규칙으로 오인하지 않게 걷어낸다.
#
# templates/user-skeleton/writing-rules.md 는 각 절에 작성 예시를 괄호로 넣어둔다.
# 사용자가 그 줄을 지우지 않으면 지금까지 그대로 프롬프트에 실려 "규칙" 으로 읽혔다.
# 실측: users/채빈/writing-rules.md 의 "- (예: 문항당 최대 3문장, 핵심만)" 이 들어가
# 스킬 Step 9 의 "글자수에 최대한 가깝게 채우되" 와 정면 충돌하고 있었다.
# 기존 코드는 파일 *전체* 가 비었을 때만 치환해서 이 경우를 못 걸렀다.
#
# 템플릿 파일 자체는 사람에게 유용한 안내이므로 지우지 않는다. 코드가 읽을 때만 걷어낸다.
_PLACEHOLDER_BULLET = re.compile(r'^\s*[-*]\s*\(\s*예시?\s*[:：]')
_EMPTY_BULLET = re.compile(r'^\s*[-*]\s*$')
_HEADING = re.compile(r'^\s*#{1,6}\s')
_REAL_BULLET = re.compile(r'^\s*[-*]\s*\S')


def strip_placeholder_rules(text: str) -> str:
    """안내용 예시 줄과, 그 결과 내용이 비게 된 소제목을 제거한다.
    남은 실제 규칙이 하나도 없으면 빈 문자열을 돌려준다 (호출자가 "규칙 없음" 으로 처리)."""
    if not text or not text.strip():
        return ''

    kept = [ln for ln in text.split('\n')
            if not _PLACEHOLDER_BULLET.match(ln) and not _EMPTY_BULLET.match(ln)]

    # 불릿이 전부 걷혀 알맹이가 없어진 소제목은 같이 뺀다. 빈 소제목만 남으면
    # 모델이 "이 항목은 의도적으로 비어 있다" 로 읽을 수 있다.
    out, i = [], 0
    while i < len(kept):
        line = kept[i]
        if _HEADING.match(line):
            j = i + 1
            has_body = False
            while j < len(kept) and not _HEADING.match(kept[j]):
                if kept[j].strip() and not kept[j].lstrip().startswith('---'):
                    has_body = True
                j += 1
            if not has_body:
                i = j
                continue
        out.append(line)
        i += 1

    result = '\n'.join(out).strip()
    return result if any(_REAL_BULLET.match(ln) for ln in out) else ''


def build_prompt(name, company, position, question, length: 'LengthSpec',
                  ctx: 'CoverletterContext', selected_experiences, want_plan: bool = True):
    """S11-7: 위치 인자 14개를 (length, ctx) 둘로 묶었다. 인자를 하나 더할 때마다
    두 호출 지점을 손으로 맞춰야 했고, 실제로 그러다 틀린 적이 있다."""
    profile_text = ctx.profile_text
    style_text = ctx.style_text
    rules_text = ctx.rules_text
    interest_text = ctx.interest_text
    company_text = ctx.company_text
    reference_texts = ctx.reference_texts
    trends_text = ctx.trends_text

    exp_dump = '\n\n'.join(f'--- users/{name}/experiences/{fn} ---\n{text}' for fn, text in selected_experiences)
    ref_dump = ('\n\n'.join(f'--- {fn} ---\n{text}' for fn, text in reference_texts)
                if reference_texts else '(로컬 레퍼런스 없음. MCP 는 이 환경에 연결되어 있지 않으므로 Step 4 는 생략)')
    # S11-1: 캐시가 없을 때 "학습 데이터 기반 일반 정보만 참고하라" 고 지시하던 것을 지웠다.
    # 그건 모델에게 기업 사실을 추론하라는 허가였고, 검토 0.1 이 지적한 우회 경로였다.
    # 이 분기에 도달했다는 것은 사용자가 "기업 정보 없이 작성" 에 명시적으로 동의했다는 뜻이다.
    # 그래도 없는 사실을 만들어 쓰게 두지는 않는다 — 쓸 수 있는 범위를 좁힌다.
    company_note = (
        f'--- companies/{company}.md ---\n{company_text}'
        if company_text.strip()
        else f'companies/{company}.md 가 없습니다. 이 기업에 대해 **검증된 자료가 하나도 없는 상태**입니다.\n'
             f'- 기업 고유의 사실(매출·규모·연혁·뉴스·수상·인물 발언·슬로건·인재상·핵심가치)을 '
             f'**단 하나도 쓰지 마세요.** 알고 있다고 생각되는 내용도 쓰지 마세요.\n'
             f'- 기업이나 업계에 대한 서술이 필요하면 아래 [지원 공고] 에 실제로 적힌 내용만 쓰세요.\n'
             f'- "{company}" 라는 이름은 써도 되지만, 그 기업이 어떤 곳인지 설명하지 마세요.\n'
             f'- 지원동기는 기업 칭찬이 아니라 **직무 자체에 대한 관심과 본인 경험의 연결**로 쓰세요.'
    )
    rules_note = strip_placeholder_rules(rules_text) or '(비어 있음 — 적용할 규칙 없음, 무시)'
    # S11-12: 외부 독자가 모를 이름을 어떻게 쓸지. 영어 여부가 아니라 독자 이해 기준이다.
    glossary_note = ''
    if ctx.glossary.explain:
        rows = '\n'.join(f'- {term} → {gloss}' for term, gloss in ctx.glossary.explain.items())
        glossary_note = (
            '\n[고유명사 — 외부 독자(채용 담당자)가 모르는 이름입니다]\n'
            '아래 이름은 그대로 쓰지 말고 설명형 표현으로 바꾸세요. 이름 자체가 꼭 필요하면\n'
            '첫 등장에 한 번만 "설명형 표현 + 이름" 으로 쓰고, 그 뒤로는 설명형만 쓰세요.\n'
            f'{rows}\n'
        )
        if ctx.glossary.allowed:
            glossary_note += ('그대로 써도 되는 이름(바꾸지 마세요): '
                               + ', '.join(ctx.glossary.allowed) + '\n')
    # S11-10 / 설계검토 B-1: 쓰기 전에 논지를 설계하게 한다. 검토 문서는 별도 선행 호출을
    # 제안하지만, 같은 응답의 **앞부분**에 받으면 호출을 늘리지 않고 대부분의 효과를 얻는다
    # (모델은 순차 생성하므로 PLAN 토큰이 실제로 ANSWER 보다 앞선다).
    # 한계: 사용자가 계획을 승인한 뒤 쓰는 구조는 아니다. 승인 단계가 필요해지면
    # output_dir=None 으로 PLAN 만 받는 호출을 먼저 두는 2단계로 올릴 수 있다.
    multi = len(selected_experiences) > 1
    # want_plan=False 면 PLAN 을 요구하지 않는다. 논지 계획은 품질 검사 기능의 일부이고,
    # 검사를 끈 호출에 계획만 받아봐야 아무도 보지 않는다 (토큰만 쓴다).
    has_job = bool(ctx.job_text.strip())
    plan_block = '' if not want_plan else f'''===PLAN===
(문단마다 한 줄. 파이프로 구분한 6칸. 설명 없이 표만)
문단id | 핵심 주장 한 문장 | 답하는 문항 요구사항 | 쓸 경험 파일명 | 그 경험이 왜 이 주장을 뒷받침하는지 | {'연결할 공고 요구조건' if has_job else '-'} | {'두 번째 경험의 역할' if multi else '-'}
예시: p1 | 반복 업무의 정확성이 전체 운영 신뢰로 이어진다고 본다 | 지원 이유 | 2026-인턴십.md | 선적서류 오류를 줄인 경험이 "정확성이 신뢰를 만든다" 는 판단의 근거다 | - | -

- 다섯 번째 칸(뒷받침하는 이유)은 비워두지 마세요. 경험과 주장 사이를 잇는 설명이 없으면
  읽는 사람은 "그래서?" 로 남습니다.
{'- 여섯 번째 칸에는 공고의 실제 요구조건을 적으세요. 억지로 연결할 필요는 없고, 해당 없으면 - 로 둡니다.' if has_job else ''}
{'- 마지막 칸: 두 경험을 모두 쓴다면 두 번째가 같은 주장을 보강하는지 / 대조하는지 / 변화 과정을 보여주는지 적으세요. "둘 다 분석 경험" 은 이유가 아닙니다. 두 번째를 지웠을 때 논증에서 무엇이 사라지는지 말할 수 없다면 쓰지 마세요.' if multi else '- 경험이 하나이므로 마지막 칸은 - 로 둡니다.'}
===END===

'''

    # S11-19: 사실 주장마다 **경험 원문에서 그대로 옮긴 인용**을 받는다. 코드가 그 구절이
    # 원본에 실제로 있는지 대조한다 (2차 설계검토 3-2). 추가 호출은 없다 — PLAN 과 같은
    # 응답 안에서 받는다. 관계가 충분한지는 여기서 묻지 않는다(검토 호출의 몫).
    evidence_block = '' if not want_plan else f'''
===EVIDENCE===
(ANSWER 의 문장 중 **본인이 과거에 한 일을 말하는 문장**마다 한 줄. 파이프로 구분한 6칸)
주장id | 문장번호 | 주장 요약 | 유형(action/result/impact) | 근거 경험 파일명 | 그 파일에서 그대로 옮긴 구절
예시: c1 | s3 | 일일 보고서를 작성했다 | action | 2026-인턴십.md | 일일 보고서를 작성하여 팀에 공유하였다

- 문장번호는 ANSWER 를 마침표 기준으로 센 순서입니다 (첫 문장이 s1).
- 마지막 칸은 **경험 파일에 적힌 글자 그대로** 옮기세요. 요약하거나 바꿔 쓰면 안 됩니다.
  코드가 원문과 대조하므로 한 글자라도 다르면 근거 없음으로 처리됩니다. {MAX_QUOTE_CHARS}자 이내.
- 원문에 그런 구절이 **없으면 그 문장을 ANSWER 에서 빼세요.** 근거를 지어내지 마세요.
- 미래 계획("~하겠습니다")과 지원 동기는 사실 주장이 아니므로 적지 않습니다.
===END===
'''

    # S11-3: 제한이 있을 때와 없을 때는 요구가 정반대다. 전자는 "넘기지 마라",
    # 후자는 "짧게 끝내지 마라, 다만 경험을 더 늘리지도 마라" 다.
    if length.bounded:
        length_line = f'글자수: {length.hard_max_chars}자 ({length.count_desc})'
        length_instruction = (
            f'- 글자수는 최대한 {length.hard_max_chars}자에 가깝게 채우되 절대 넘기지 마세요 '
            f'(여유를 약간 두는 편이 넘기는 것보다 낫습니다).'
        )
    else:
        length_line = f'글자수: 제한 없음 (세는 기준은 {length.count_desc})'
        length_instruction = (
            '- 글자수 제한이 없습니다. 짧게 요약하지 마세요.\n'
            '- 다만 **제한이 없다는 것은 경험을 더 끌어오라는 뜻이 아닙니다.** 주어진 경험의\n'
            '  배경, 그때 본인이 내린 판단과 그 이유, 실행 과정, 결과, 직무와의 연결을\n'
            '  충분히 깊게 쓰세요.\n'
            '- 분량을 채우려고 추측이나 일반론을 덧붙이지 마세요. 쓸 근거가 떨어지면 거기서 끝내세요.'
        )
    # 고도화(2026-09-27): lib/trends.py 가 한 달에 한 번 자동 조사해두는 최신 자소서 작성
    # 트렌드. 참고용일 뿐 writing-style.md/writing-rules.md 보다 우선하지 않는다 — Claude.md
    # 의 "반드시 해당 사용자의 writing-style.md 어투를 따를 것" 절대 원칙이 이 트렌드 참고보다
    # 위에 있다는 걸 프롬프트에도 명시한다(안 그러면 트렌드 문구를 어투보다 우선할 위험이 있다).
    trends_note = (
        trends_text.strip()
        if trends_text.strip()
        else '(아직 조사된 트렌드 없음 — 무시)'
    )
    # S11-8 / 검토 0.3: 예전엔 공고 레코드를 통째로 들고 있으면서도 회사명과 직무명
    # 두 문자열만 넘겼다. 프롬프트가 "직무 적합성" 을 요구하면서 정작 그 직무의 실제
    # 요구조건을 주지 않은 셈이다. 단독 생성(공고 없이)에서는 빈 문자열이라 기존 동작 그대로다.
    job_block = (
        f'''[지원 공고 — 검증된 1차 자료입니다. 사실 출처로만 쓰고, 여기 없는 요구사항을
지어내지 마세요. 아래 조건 중 본인 경험으로 실제 뒷받침할 수 있는 것만 연결하세요]
{ctx.job_text.strip()}

'''
        if ctx.job_text.strip() else ''
    )

    return f"""[요청]
사용자: {name}
기업명: {company}
직무명: {position}
문항: {question}
{length_line}

Step 1(파일 읽기)은 아래에 이미 첨부되어 있습니다. Step 5~7(문항 분석, 유형 분류, 경험 선택)은
코드가 이미 끝냈습니다 — 아래 [이미 선택된 경험]만 보고 Step 8(문단 작성)을 하세요.
Step 2, Step 4 는 아래 안내를 따르세요. Step 9(출력 형식), Step 10(파일 저장)은 이 호출에서 스킵합니다
(코드가 별도로 처리합니다). 대신 아래 구분자 형식으로만 답하세요.

--- users/{name}/profile.md ---
{profile_text}

--- users/{name}/writing-style.md ---
{style_text}

--- users/{name}/writing-rules.md ---
{rules_note}

--- users/{name}/관심직무.md ---
{interest_text}

{glossary_note}
[Step 2: 기업 정보]
{company_note}

{job_block}
[Step 4: 합격 사례 레퍼런스]
{ref_dump}
레퍼런스가 있어도 문장·표현·사례를 그대로 쓰지 마세요. 구조·전개 방식만 참고하고,
표현과 사례는 반드시 아래 experiences/ 원문에서만 가져오세요.

[참고: 최신 자소서 작성 트렌드 — 참고용일 뿐입니다. writing-style.md/writing-rules.md 와
충돌하면 반드시 writing-style.md/writing-rules.md 를 따르세요]
{trends_note}

[이미 선택된 경험 — 이 안에서만 쓰세요. 다른 경험을 지어내거나 끌어오지 마세요]
{exp_dump}

[지시]
- 위에 주어진 경험만 사용하세요. 여러 경험이 있으면 자연스럽게 연결해서 서술하세요.
- 선택한 경험 파일에 "⚠️ 오래된 경험" 표시가 있으면, 이 호출은 되물을 수 없으므로 일단 그대로 사용하되
  META 블록에 그 사실을 명시하세요.
{length_instruction}
- bullet point 금지, 문단 형식.

[출력 형식 — 반드시 이 형식만 사용, 다른 설명 붙이지 말 것]
{plan_block}===ANSWER===
(작성된 문단. 이것만 글자수 계산에 사용되니 다른 텍스트를 섞지 마세요)
===END===

===META===
참조한 경험: [경험명] ([파일명])
참조한 기업 정보: [companies/{company}.md 또는 "없음 (추론)"]
참조한 레퍼런스: [파일명 또는 "없음"]
오래된 경험 사용 여부: [예/아니오]
===END===
{evidence_block}"""


def build_retry_prompt(length: 'LengthSpec', current_count, company=''):
    """초과했을 때뿐 아니라, 많이 모자랄 때도 부른다 (스킬 Step 9: "여유 있게 짧게 쓰는 것 금지").
    목표를 하나의 정확한 숫자로 주고, 그 숫자에 맞춰 문장 단위로 더하거나 빼라고 구체적으로 지시한다.
    "권장"처럼 약한 표현을 쓰면 모델이 충분히 맞추지 않는 경향이 실측에서 확인되어, 지시를 강하게 바꿨다.
    """
    char_limit = length.hard_max_chars
    count_desc = length.count_desc
    diff = current_count - char_limit
    target = max(char_limit - TARGET_UNDERSHOOT, 1)

    if diff > 0:
        direction = (
            f'방금 작성한 ANSWER 가 {char_limit}자({count_desc}) 제한을 {diff}자 초과했습니다 (실제 {current_count}자).\n'
            f'같은 경험, 같은 핵심 내용을 유지하되 문장 1~2개를 통째로 줄이거나 수식어를 정리해서 압축하세요.'
        )
    else:
        direction = (
            f'방금 작성한 ANSWER 는 {char_limit}자({count_desc}) 목표보다 {-diff}자 모자랍니다 (실제 {current_count}자).\n'
            f'같은 경험 안에서 아직 안 쓴 구체적 사실(수치, 과정, 계기 등)을 문장으로 추가하세요. '
            f'없는 내용을 지어내지 말고, 이미 첨부된 experiences/ 원문에 있는 사실만 더 끌어와 채우세요.'
        )

    return f"""{direction}
목표는 정확히 {target}자 근처({count_desc} 기준)입니다. 다 쓴 뒤 스스로 글자 수를 세어 {target}자에 가까운지 확인하고,
차이가 크면 그 자리에서 한 번 더 다듬어서 최종 결과만 아래 형식으로 답하세요.

===ANSWER===
(다시 쓴 문단)
===END===

===META===
참조한 경험: [경험명] ([파일명])
참조한 기업 정보: [companies/{company}.md 또는 "없음 (추론)"]
참조한 레퍼런스: [파일명 또는 "없음"]
오래된 경험 사용 여부: [예/아니오]
===END===
"""


_CITED_LINE = re.compile(r'^참조한 경험:\s*(.+)$', re.M)
_MD_FILENAME = re.compile(r'([^\s()\[\],]+\.md)')


def validate_cited_experiences(meta_text: str, supplied_filenames) -> Tuple[List[str], List[str]]:
    """META 의 "참조한 경험" 줄에 적힌 파일명이 실제로 프롬프트에 실린 파일인지 대조한다.
    (인용된 목록, 제공되지 않은 목록) 을 돌려준다.

    lib/matcher.py 의 _validate_evidence 와 같은 원칙이다 — 모델의 자기보고를 믿지 않고
    코드가 실재 여부를 확인한다. 거기서는 근거 파일이 없는 판정을 미충족으로 강등하고,
    여기서는 status 를 review_required 로 내린다.

    설계검토 A-4 가 요구한 selected(코드가 준 것) vs actually_cited(글이 쓴 것) 분리를
    이 15줄로 얻는다. 다만 **파일명 대조일 뿐**이다 — 모델이 파일명을 맞게 적고도 그 경험에
    없는 내용을 썼는지는 알 수 없다. 그 판정은 S11-10 의 검토 호출이 맡는다.

    모델이 파일명을 아예 안 적었으면(경험명만 적는 경우) 대조할 대상이 없으므로 빈 목록을
    돌려준다. 형식을 안 지켰다는 이유로 감점하지는 않는다.
    """
    supplied = set(supplied_filenames)
    cited = []
    for line in _CITED_LINE.findall(meta_text or ''):
        for fn in _MD_FILENAME.findall(line):
            if fn not in cited:
                cited.append(fn)
    unknown = [fn for fn in cited if fn not in supplied]
    return cited, unknown


_DATE_IN_TEXT = re.compile(r'(\d{4})[-.년]\s*(\d{1,2})[-.월]\s*(\d{1,2})')
_URL = re.compile(r'https?://\S+')


def check_company_evidence(company: str, text: str, today: Optional[date] = None) -> List[str]:
    """기업 자료가 "파일이 있다" 보다 강한 조건을 만족하는지 본다 (설계검토 A-4).

    예전에는 `company_text.strip()` 한 줄이 전부여서, 빈 파일만 걸렀다. 출처 없는 요약,
    다른 기업 내용이 담긴 파일, 2년 전 자료가 전부 "조사 완료" 로 통과했다.

    돌려주는 사유 목록이 비면 통과다. 'mismatch' 는 호출자가 차단하고,
    나머지(stale/thin)는 경고로 쓴다 — 오래됐다고 못 쓰게 하면 오히려 추론을 유도한다.
    """
    problems = []
    if not text or not text.strip():
        return ['empty']

    # 1. 다른 기업 파일인가. 파일명만 맞고 내용이 다른 경우를 잡는다.
    #    기업명은 ㈜, (주), 공백, 영문 병기 등 변형이 많아 핵심 음절만 비교한다.
    core = re.sub(r'[\s㈜()（）주식회사\[\]]+', '', company)
    if core and core not in re.sub(r'[\s㈜()（）주식회사\[\]]+', '', text):
        problems.append('mismatch')

    # 2. 언제 확인한 자료인가. 날짜가 아예 없거나 너무 오래됐으면 알린다.
    today = today or date.today()
    dates = []
    for y, m, d in _DATE_IN_TEXT.findall(text):
        try:
            dates.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    if not dates:
        problems.append('undated')
    elif (today - max(dates)).days > COMPANY_EVIDENCE_MAX_AGE_DAYS:
        problems.append('stale')

    # 3. 출처가 있는가. URL 이 하나도 없으면 사실을 대조할 방법이 없다.
    if not _URL.search(text):
        problems.append('no_source')
    return problems


# 숫자는 기계적으로 대조할 수 있다. 다만 **경보지 허위 판정이 아니다** — 단위 환산,
# 반올림, 파생 수치(둘을 더한 값 등)는 원문에 그대로 없을 수 있다 (설계검토 B-2).
_NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
# 기관명은 한국어 NER 없이 일반적으로 잡기 어렵다. 접미사가 붙은 것만 보수적으로 본다.
_ORG_SUFFIX = re.compile(r'[가-힣A-Za-z0-9]{2,}(?:대학교|대학|고등학교|공사|공단|협회|학회|재단|'
                          r'연구소|연구원|진흥원|센터|병원|은행|그룹|상사|물산)')
# 날짜는 "2026.01.12" 로 적힌 원문이 _NUMBER 에 "2026.01" 로 잡혀, 초안의 "2026년" 과
# 어긋난다. 연/월/일을 따로 등록해 이 오탐을 막는다 (실제 자소서를 쓰다 발견했다).
_DATE_TOKENS = re.compile(r'(\d{4})\s*[.\-/년]\s*(\d{1,2})(?:\s*[.\-/월]\s*(\d{1,2}))?')


def _normalize_number(s: str) -> str:
    s = s.replace(',', '')
    return s[:-1] if s.endswith('.') else s


# ---------------------------------------------------------------------------
# 출처별 증명 범위 (S11-18, 2차 설계검토 3-1)
#
# 예전엔 경험·공고·기업 자료를 한 덩어리로 이어 붙여 "이 문자열이 어딘가 있는가" 만
# 봤다. 그러면 공고에 SAP 가 적혀 있다는 이유로 "SAP 로 재고를 관리했습니다" 가 통과한다.
# **어딘가에 같은 단어가 있는 것과 그 주장이 참인 것은 다르다.**
#
# 출처마다 증명할 수 있는 것이 다르다:
#   experience  본인의 행동·역할·성과          ← 자기 주장을 증명하는 유일한 출처
#   company     기업에 관한 사실
#   job         지원 직무의 요구조건
#   question    문항에 포함된 표현
#   glossary    같은 대상을 가리키는 이름 표기  (표기만, 그 활동을 했다는 증거는 아님)
# ---------------------------------------------------------------------------

class Source(NamedTuple):
    kind: str
    text: str


SOURCE_PROVES = {
    'experience': '본인의 행동과 역할, 성과',
    'company': '기업에 관한 사실',
    'job': '지원 직무의 요구조건',
    'question': '문항에 포함된 표현',
    'glossary': '같은 대상을 가리키는 이름 표기',
}
# 본인이 무엇을 했다는 주장을 뒷받침할 수 있는 출처는 이것뿐이다.
SELF_CLAIM_SOURCES = frozenset({'experience'})


def _as_sources(sources) -> List[Source]:
    """문자열 리스트도 받는다. 유형을 안 밝히면 experience 로 본다 — 기존 호출자의
    동작을 그대로 두기 위해서다(가장 관대한 쪽)."""
    out = []
    for s in sources or []:
        out.append(s if isinstance(s, Source) else Source('experience', s or ''))
    return out


# 본인이 과거에 한 일을 말하는 문장. 미래 계획과 포부는 사실 주장이 아니므로 뺀다
# (검토 3-2: "모든 문장이 사실 주장은 아니다").
# 지각 동사(보았다, 들었다, 알았다)는 뺀다. **무언가를 본 것은 그것을 한 것이 아니다.**
# 실제 자소서를 돌리다 "퀴네앤드나겔이 2030년까지 ... 밝힌 것을 보았습니다" 가 자기 주장으로
# 잡혀서 발견했다 — 기업 사실을 기업 것으로 제대로 귀속한 문장인데 오탐이 났다.
_PAST_SELF_ACTION = re.compile(
    r'(했|하였|맡았|받았|만들었|썼|배웠|익혔|다뤘|다루었|얻었|냈|이루었|이뤘)습니다')
_FUTURE_OR_INTENT = re.compile(r'(겠습니다|고 싶습니다|하려 합니다|할 것입니다|예정입니다|싶습니다)')


# 근거가 필요한 문장은 "내가 한 일" 보다 넓다. **영향 주장**("보고서가 기준이 되었습니다")
# 은 주어가 내가 아니지만 가장 지어내기 쉬운 종류다 — 실제로 2026-10-04 에 잡힌 거짓 주장이
# 정확히 이 형태였다. 그래서 커버리지 검사는 과거형 서술 전체를 본다.
# '했' 은 '하였' 의 축약이라 '었/였' 글자를 담지 않는다. 따로 넣어야 한다.
_PAST_DECLARATIVE = re.compile(r'(았|었|였|했)습니다')
# 지원 동기는 사실 주장이 아니다. "지원하게 되었습니다" 류를 뺀다.
_MOTIVE = re.compile(r'(지원|결정|선택)(하게 |해 |을 |를 )?(되었|했)습니다')
# 명사절에 붙은 계사는 해석이지 사실 주장이 아니다 — "~하기 위해서였습니다",
# "가장 자주 틀리는 지점이었습니다", "배운 것은 ~라는 점이었습니다".
# (실제 자소서에 커버리지 검사를 돌려 발견한 오탐 패턴이다.)
_REFLECTION = re.compile(r'(것|점|이유|때문|지점|까닭|위해서|셈)(이었|였|었)습니다')


def needs_evidence(sentence: str) -> bool:
    """이 문장이 근거를 대야 하는 사실 주장인가 (S11-19 커버리지 검사용)."""
    if (_FUTURE_OR_INTENT.search(sentence) or _MOTIVE.search(sentence)
            or _REFLECTION.search(sentence)):
        return False
    return bool(_PAST_DECLARATIVE.search(sentence))


def is_self_claim(sentence: str) -> bool:
    """이 문장이 '내가 그것을 했다' 는 주장인가. 포부와 계획은 아니다."""
    if _FUTURE_OR_INTENT.search(sentence):
        return False
    return bool(_PAST_SELF_ACTION.search(sentence))


# 공고에 적힌 도구·시스템 이름은 ASCII 로 쓰이는 경우가 많다(SAP, WMS, ERP, Excel, SQL).
# 한국어 일반명사까지 일반적으로 뽑으려면 NER 이 필요하므로 여기서는 손대지 않는다 —
# 못 잡는 범위를 넓히는 것보다 잡는 범위를 정확히 아는 쪽이 낫다.
_ASCII_TERM = re.compile(r'[A-Za-z][A-Za-z0-9+#.]{1,19}')
_TERM_NOISE = frozenset({
    'and', 'or', 'of', 'in', 'for', 'with', 'to', 'the', 'a', 'an', 'at', 'on', 'by',
    'is', 'are', 'be', 'as', 'it', 'etc', 'http', 'https', 'www', 'com', 'co', 'kr',
})


def jd_terms(requirements: List[str]) -> List[str]:
    """공고의 필수·우대조건에서 대조 가능한 도구/시스템 이름을 뽑는다."""
    seen, out = set(), []
    for line in requirements or []:
        for m in _ASCII_TERM.findall(line or ''):
            key = m.lower().strip('.')
            if len(key) < 2 or key in _TERM_NOISE or key in seen:
                continue
            seen.add(key)
            out.append(m.strip('.'))
    return out


def check_jd_claims(answer: str, terms: List[str],
                    experience_texts: List[str]) -> List[str]:
    """공고에 적힌 도구를 **본인이 써 봤다고 말하면서** 경험 파일에 근거가 없는 경우를 찾는다.

    검토 3-1 의 SAP 예시가 이것이다. 공고에 SAP 가 있다는 사실은 그 직무가 SAP 를
    요구한다는 것을 증명할 뿐, 지원자가 SAP 를 써 봤다는 것을 증명하지 않는다.

    "입사 후 익히겠습니다" 같은 미래 계획은 주장이 아니므로 통과시킨다.
    """
    exp = re.sub(r'\s+', '', '\n'.join(experience_texts or [])).lower()
    found = []
    for sentence in split_sentences(answer):
        if not is_self_claim(sentence):
            continue
        low = sentence.lower()
        for term in terms or []:
            t = term.lower()
            if t in low and t not in exp and term not in found:
                found.append(term)
    return found


def _number_pool(text: str) -> set:
    pool = {_normalize_number(n) for n in _NUMBER.findall(text)}
    for parts in _DATE_TOKENS.findall(text):
        pool.update(p.lstrip('0') or '0' for p in parts if p)
        pool.update(p for p in parts if p)
    return pool


def _pools_by_kind(sources) -> dict:
    """출처 유형별로 (수치 집합, 공백 제거 텍스트) 를 만든다."""
    grouped = {}
    for s in _as_sources(sources):
        grouped.setdefault(s.kind, []).append(s.text)
    return {kind: (_number_pool('\n'.join(texts)), re.sub(r'\s+', '', '\n'.join(texts)))
            for kind, texts in grouped.items()}


def find_unverified_facts(answer: str, sources) -> Tuple[List[str], List[str]]:
    """초안에 나온 수치와 기관명 중 **어떤 출처에도** 없는 것을 찾는다.
    (미확인 수치, 미확인 기관명). 설계검토 A-1 의 "수치·기관명·역할의 불일치를 탐지".

    sources 는 문자열 리스트여도 되고 Source(kind, text) 리스트여도 된다.
    출처 유형을 구분해 보려면 find_jd_only_facts 를 함께 쓴다 (S11-18).
    """
    pools = _pools_by_kind(sources)
    hay_numbers = set().union(*(p[0] for p in pools.values())) if pools else set()
    hay_text = ''.join(p[1] for p in pools.values())

    numbers, orgs = [], []
    for raw in _NUMBER.findall(answer):
        n = _normalize_number(raw)
        # 한 자리 수는 "첫 번째", "두 가지" 처럼 서술에서 자연히 나온다. 노이즈가 커서 뺀다.
        if len(n.replace('.', '')) < 2:
            continue
        if n not in hay_numbers and n not in numbers:
            numbers.append(n)
    for org in _ORG_SUFFIX.findall(answer):
        if re.sub(r'\s+', '', org) not in hay_text and org not in orgs:
            orgs.append(org)
    return numbers, orgs


def find_jd_only_facts(answer: str, sources) -> List[str]:
    """**본인이 했다고 말하는 문장**에 쓰였는데, 경험이 아니라 공고나 기업 자료에만
    근거가 있는 수치와 기관명을 찾는다 (S11-18, 2차 설계검토 3-1).

    find_unverified_facts 는 "어딘가에 있으면 통과" 다. 그 '어딘가' 가 공고일 때가
    문제다 — 공고는 직무 요구조건을 증명할 뿐 지원자가 그 일을 했다는 증거가 아니다.

    경보지 판정이 아니다. 경험 파일에 없는 표기로 같은 사실을 말했을 수 있다.
    """
    pools = _pools_by_kind(sources)
    own_numbers, own_text = pools.get('experience', (set(), ''))
    other = [(k, v) for k, v in pools.items() if k not in SELF_CLAIM_SOURCES]
    if not other:
        return []

    found = []
    for sentence in split_sentences(answer):
        if not is_self_claim(sentence):
            continue
        for raw in _NUMBER.findall(sentence):
            n = _normalize_number(raw)
            if len(n.replace('.', '')) < 2 or n in own_numbers:
                continue
            for kind, (nums, _) in other:
                if n in nums:
                    label = f'{n} ({SOURCE_PROVES.get(kind, kind)}에만 있음)'
                    if label not in found:
                        found.append(label)
                    break
        for org in _ORG_SUFFIX.findall(sentence):
            flat = re.sub(r'\s+', '', org)
            if flat in own_text:
                continue
            for kind, (_, text) in other:
                if flat in text:
                    label = f'{org} ({SOURCE_PROVES.get(kind, kind)}에만 있음)'
                    if label not in found:
                        found.append(label)
                    break
    return found


# 결정적으로 검사할 수 있는 개인 규칙만 등록한다. writing-rules.md 에 그 규칙이 실제로
# 적혀 있을 때만 검사한다 — 모든 사용자에게 강제할 규칙이 아니기 때문이다.
# (설계검토 C-2 의 구조화된 규칙 스키마로 가기 전의 최소 형태다.)
CHECKABLE_RULES = [
    {
        'id': 'no-middot',
        'detect': lambda rules: '가운뎃점' in rules and '금지' in rules,
        'violation': lambda text: '·' in text,
        'label': '가운뎃점(·)',
    },
]


def check_personal_rules(answer: str, rules_text: str) -> List[str]:
    """개인 규칙 중 코드가 판정할 수 있는 것만 검사한다.
    예전엔 "가운뎃점 금지" 가 writing-rules.md 에 있어도 프롬프트에만 맡겨져 있었다."""
    found = []
    for rule in CHECKABLE_RULES:
        if rule['detect'](rules_text or '') and rule['violation'](answer):
            found.append(rule['label'])
    return found


# ---------------------------------------------------------------------------
# 문서 구조 (S11-13, 설계검토 C-3)
#
# 저장된 자소서는 문항별 append 구조다.
#
#   # <기업> <직무>
#
#   ## <문항>
#   글자수: ...
#
#   <본문>
#
#   ---
#   <META>
#   작성일: ...
#
# 수정은 "그 문항 구간만" 바꾸고 **문서 전체를 돌려준다.** 모델이 발췌만 내놓아도
# 서버가 전체를 재구성하므로, "수정한 부분만 보여주지 말라" 는 선호를 모델이 매번
# 기억할 필요가 없다 (설계검토 C-3).
# ---------------------------------------------------------------------------

_SECTION_SPLIT = re.compile(r'^## ', re.M)
_CITED_IN_SECTION = re.compile(r'^참조한 경험:\s*(.+)$', re.M)


def split_document(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """(머리말, [(문항, 구간 전문), ...]). 구간 전문은 "## " 를 포함하지 않는다."""
    if not text:
        return '', []
    parts = _SECTION_SPLIT.split(text)
    head = parts[0].rstrip()
    sections = []
    for body in parts[1:]:
        lines = body.split('\n')
        sections.append((lines[0].strip(), body.rstrip()))
    return head, sections


def find_section(text: str, question: str) -> Optional[str]:
    for q, body in split_document(text)[1]:
        if q == question.strip():
            return body
    return None


def section_answer(section: str) -> str:
    """구간에서 본문만 뽑는다. 글자수 줄과 ⚠️ 배너 뒤부터 --- 앞까지다."""
    body = section.split('\n', 1)[1] if '\n' in section else ''
    body = body.split('\n---\n', 1)[0]
    lines = [ln for ln in body.split('\n')
             if not ln.startswith('글자수:') and not ln.startswith('⚠️')]
    return '\n'.join(lines).strip()


def section_cited_files(section: str) -> List[str]:
    """그 구간이 어떤 경험으로 쓰였는지. 수정할 때 허용 범위를 그대로 유지하기 위해 쓴다."""
    out = []
    for line in _CITED_IN_SECTION.findall(section):
        for fn in _MD_FILENAME.findall(line):
            if fn not in out:
                out.append(fn)
    return out


def replace_section(text: str, question: str, new_section: str) -> Optional[str]:
    """해당 문항 구간만 바꾼 **문서 전체**를 돌려준다. 못 찾으면 None."""
    head, sections = split_document(text)
    target = question.strip()
    if not any(q == target for q, _ in sections):
        return None
    rebuilt = [head] if head else []
    for q, body in sections:
        rebuilt.append('## ' + (new_section.rstrip() if q == target else body))
    return '\n\n'.join(rebuilt).rstrip() + '\n'


def build_revise_prompt(question: str, current_answer: str, instruction: str,
                         length: 'LengthSpec', allowed_filenames) -> str:
    """사용자가 말한 수정 지시를 그대로 전달하되, 허용 범위를 코드가 못박는다."""
    limit = (f'{length.hard_max_chars}자 ({length.count_desc}) 이내'
             if length.bounded else f'제한 없음 (세는 기준은 {length.count_desc})')
    return f"""아래 자소서 문항의 답변을 사용자의 지시대로 고쳐 주세요.

[문항]
{question}

[현재 답변]
{current_answer}

[사용자의 수정 지시]
{instruction}

[지킬 것]
- 쓸 수 있는 경험은 그대로입니다: {', '.join(allowed_filenames) or '(원문에 기록 없음)'}.
  새 경험이나 새 사실을 넣지 마세요. 수치와 기관명도 기존 경험에 있는 것만 씁니다.
- 지시와 무관한 부분은 바꾸지 마세요.
- 글자수: {limit}
- bullet point 금지, 문단 형식.

[출력 형식 — 반드시 이 형식만]
===ANSWER===
(고친 답변 전문. 발췌가 아니라 그 문항의 답변 전체)
===END===

===META===
참조한 경험: [경험명] ([파일명])
수정 요약: [무엇을 왜 바꿨는지 한 줄]
===END===
"""


# ---------------------------------------------------------------------------
# 고유명사 처리 (S11-12, 설계검토 C-2 / D-3)
#
# 기존 규칙은 "경험 DB 의 영어 워딩을 한글로 풀어 쓸 것" 이었다. 기준이 언어라서
# 한국어 소모임명("다온", "새싹기획단")이나 내부 프로그램 코드명은 그대로 통과했다.
# 검토 문서의 지적대로 기준을 **독자 이해**로 바꾸고, 개인 사전을 따로 둔다.
#
#   users/[이름]/glossary.md  — 풀어 쓸 이름 / 그대로 쓸 이름 / 전문용어
#
# 코드는 "첫 등장에 설명이 붙었는가" 만 본다. 무엇을 풀어 쓸지는 사람이 정한다.
# ---------------------------------------------------------------------------

_GLOSSARY_ROW = re.compile(r'^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|')
_SECTION = re.compile(r'^##\s*(.+?)\s*$', re.M)


@dataclass
class Glossary:
    """term → 자소서에서 쓸 설명형 표현."""
    explain: dict = field(default_factory=dict)   # 풀어 써야 하는 이름
    allowed: List[str] = field(default_factory=list)   # 그대로 써도 되는 이름(참고용)

    def __bool__(self):
        return bool(self.explain or self.allowed)


def parse_glossary(text: str) -> Glossary:
    g = Glossary()
    if not text:
        return g
    section = ''
    for line in text.split('\n'):
        m = _SECTION.match(line)
        if m:
            section = m.group(1)
            continue
        if '풀어' in section:
            row = _GLOSSARY_ROW.match(line)
            if not row:
                continue
            term, gloss = row.group(1).strip(), row.group(2).strip()
            if not term or term in ('원래 이름', '---') or set(term) <= {'-', ' '}:
                continue
            if gloss and gloss not in ('자소서 표현', '---'):
                g.explain[term] = gloss
        elif '그대로' in section or '전문용어' in section:
            for item in re.findall(r'^-\s*(.+?)\s*$', line):
                g.allowed.extend(x.strip() for x in item.split(',') if x.strip())
    return g


def load_glossary(user_root: Path) -> Glossary:
    return parse_glossary(read_if_exists(user_root / 'glossary.md'))


def _gloss_core(gloss: str) -> str:
    """설명형 표현에서 대조에 쓸 핵심 부분. 괄호 주석과 조사를 떼어낸다."""
    core = re.split(r'[(（]', gloss)[0].strip()
    return core


def check_obscure_names(answer: str, glossary: Glossary) -> List[str]:
    """사전에 등록된 이름이 **설명 없이** 쓰였는지 본다.

    판정은 단순하다 — 그 이름이 본문에 있으면 설명 표현도 본문에 있어야 한다.
    "교내 데이터 분석 소모임 DIVE" 처럼 둘 다 쓰면 통과하고, "DIVE" 만 쓰면 걸린다.
    설명으로 완전히 대체한 경우(이름이 아예 없음)도 당연히 통과다.
    """
    found = []
    for term, gloss in glossary.explain.items():
        if term not in answer:
            continue
        core = _gloss_core(gloss)
        if core and core in answer:
            continue
        found.append(term)
    return found


def parse_answer(text):
    a = _ANSWER_BLOCK.search(text)
    m = _META_BLOCK.search(text)
    answer = a.group('body').strip() if a else ''
    meta = m.group('body').strip() if m else ''
    return answer, meta


# ---------------------------------------------------------------------------
# 품질 검사 (S11-10, 설계검토 B)
#
# 호출 수를 설계 제약으로 둔다. 2026-09-27 세션이 크레딧 소진으로 끊긴 적이 있다
# (PROGRESS.md). 그래서 검토 문서가 제안한 "별도 선행 호출로 논지 계획" 대신 같은
# 응답의 앞부분에 PLAN 을 받고(추가 0회), 검토 1회 + 수정 1회까지만 쓴다.
# 최악의 경우 초안 1 + 재시도 1 + 검토 1 + 수정 1 = 4회. 끄면 기존과 같은 2회.
#
# 검토 문서가 인용한 "LLMs Cannot Self-Correct Reasoning Yet" 때문에라도, 검토 호출
# 하나를 더하면 품질이 보장된다고 보지 않는다. 결정적 린터가 먼저고, 검토는 그 위에 얹는다.
# ---------------------------------------------------------------------------

# 한국어 문장 분리. 약어·소수점·인용을 완벽히 가르지는 못한다(설계검토 B-2 가 지적한 바).
# 자소서는 bullet 을 금지하고 평문 문단만 쓰므로 이 정도로 충분하다고 보고, 대신 검토가
# 돌려준 문장 ID 가 우리가 보낸 집합과 다르면 검토 자체를 실패 처리한다.
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')


def split_sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def parse_plan(text: str) -> List[dict]:
    """===PLAN=== 블록을 파싱한다. 형식을 안 지켜도 예외를 내지 않는다 —
    계획이 없다는 사실 자체가 린터의 판정 대상이다."""
    m = _PLAN_BLOCK.search(text or '')
    if not m:
        return []
    rows = []
    for line in m.group('body').split('\n'):
        line = line.strip()
        if not line or line.startswith('(') or line.startswith('예시'):
            continue
        cells = [c.strip() for c in line.split('|')]
        if len(cells) < 4 or cells[0].startswith('문단id'):
            continue
        def cell(i):
            v = cells[i].strip() if len(cells) > i else ''
            return '' if v in ('-', '—', 'N/A') else v

        rows.append({'id': cells[0], 'claim': cells[1], 'requirement': cells[2],
                      'experiences': [f for f in _MD_FILENAME.findall(cells[3])],
                      # S11-14: 설계검토 B-1 의 "연결 설명(warrant)" 과 JD 요구조건.
                      # 근거와 주장 사이를 잇는 설명이 없으면 읽는 사람은 "그래서?" 로 남는다.
                      'warrant': cell(4),
                      'job_requirement': cell(5),
                      'second_role': cell(6)})
    return rows


def lint_plan(plan: List[dict], supplied_filenames) -> List[str]:
    """결정적 린터. 모델 호출 없이 계획만 보고 판정한다."""
    issues = []
    if not plan:
        return [ISSUE_PLAN_MISSING]
    supplied = set(supplied_filenames)
    if any(fn not in supplied for row in plan for fn in row['experiences']):
        issues.append(ISSUE_PLAN_UNKNOWN_EXPERIENCE)
    # S11-14: 근거가 왜 주장을 지지하는지 설명하지 못하면 논증이 아니라 나열이다.
    if any(not row['warrant'] for row in plan):
        issues.append(ISSUE_PLAN_MISSING_WARRANT)
    # 경험을 둘 쓰면서 두 번째의 역할을 못 적었다면, 왜 둘인지 설명하지 못한 것이다.
    if len(supplied) > 1:
        used = {fn for row in plan for fn in row['experiences']}
        roles = ' '.join(row['second_role'] for row in plan).strip(' -')
        if len(used) > 1 and not roles:
            issues.append(ISSUE_SECOND_EXPERIENCE_UNJUSTIFIED)
    return issues


def parse_evidence(text: str) -> List[dict]:
    """===EVIDENCE=== 블록을 파싱한다 (S11-19, 2차 설계검토 3-2).

    형식을 안 지켜도 예외를 내지 않는다 — 근거를 못 댔다는 사실 자체가 판정 대상이다.
    """
    m = _EVIDENCE_BLOCK.search(text or '')
    if not m:
        return []
    rows = []
    for line in m.group('body').split('\n'):
        line = line.strip()
        if not line or line.startswith('(') or line.startswith('예시'):
            continue
        cells = [c.strip() for c in line.split('|')]
        if len(cells) < 5 or cells[0].startswith('주장id'):
            continue

        def cell(i):
            v = cells[i].strip() if len(cells) > i else ''
            return '' if v in ('-', '—', 'N/A') else v

        sent = cell(1).lower().lstrip('s')
        rows.append({
            'id': cells[0],
            'sentence': int(sent) if sent.isdigit() else None,
            'claim': cell(2),
            'claim_type': cell(3),
            'file': (_MD_FILENAME.findall(cell(4)) or [cell(4)])[0],
            'quote': cell(5),
        })
    return rows


def _flat(s: str) -> str:
    return re.sub(r'\s+', '', s or '')


def lint_evidence(rows: List[dict], answer: str, experience_texts: dict) -> List[str]:
    """**코드가 인용문을 원본과 대조한다.** 검토 3-2 의 3단계가 이것이다.

    모델이 "이 주장은 저 파일의 이 구절이 근거다" 라고 말하면, 그 구절이 그 파일에
    실제로 있는지는 코드가 확인할 수 있다. 모델의 자기보고를 믿지 않는다
    (lib/matcher.py 의 _validate_evidence 와 같은 원칙).

    주장과 근거의 **관계가 충분한지**는 여기서 판정하지 않는다 — 그건 의미 판단이고
    skills/review-coverletter.md 의 검토 호출이 맡는다. 여기서는 인용이 실재하는지만 본다.
    """
    issues = []
    sentences = split_sentences(answer)
    # 근거가 필요한 문장이 애초에 없으면 빈 블록이 정답이다. 지원 동기와 포부만 있는
    # 문항에 "근거를 안 댔다" 고 하면 오탐이다 (검토 3-2: 모든 문장이 사실 주장은 아니다).
    needs = [i for i, s in enumerate(sentences, 1) if needs_evidence(s)]
    if not rows:
        return [ISSUE_EVIDENCE_MISSING] if needs else []

    for row in rows:
        if not row['sentence'] or not (1 <= row['sentence'] <= len(sentences)):
            if ISSUE_EVIDENCE_BAD_SENTENCE not in issues:
                issues.append(ISSUE_EVIDENCE_BAD_SENTENCE)
        src = experience_texts.get(row['file'])
        if src is None:
            if ISSUE_EVIDENCE_UNKNOWN_FILE not in issues:
                issues.append(ISSUE_EVIDENCE_UNKNOWN_FILE)
            continue
        quote = _flat(row['quote'])
        # 빈 인용이나 원문에 없는 인용은 근거가 아니다.
        if not quote or quote not in _flat(src):
            if ISSUE_EVIDENCE_QUOTE_NOT_FOUND not in issues:
                issues.append(ISSUE_EVIDENCE_QUOTE_NOT_FOUND)

    # 커버리지: 본인이 했다고 쓴 문장 중 근거 행이 없는 것 (S11-18 의 is_self_claim 재사용).
    covered = {r['sentence'] for r in rows if r['sentence']}
    if [i for i in needs if i not in covered]:
        issues.append(ISSUE_EVIDENCE_UNCOVERED)
    return issues


def evidence_report(rows: List[dict], experience_texts: dict) -> List[dict]:
    """화면과 실행 기록에 남길 주장별 대조 결과. verdict 는 인용의 실재 여부만 말한다."""
    out = []
    for row in rows:
        src = experience_texts.get(row['file'])
        if src is None:
            verdict = 'unknown_file'
        elif row['quote'] and _flat(row['quote']) in _flat(src):
            verdict = 'quote_found'
        else:
            verdict = 'quote_not_found'
        out.append({**row, 'verdict': verdict})
    return out


def build_review_input(question: str, draft: str, *, job_text: str = '',
                        company_text: str = '', allowed_experiences=()) -> str:
    """검토 호출의 입력. **계획(PLAN)은 넣지 않는다** — 설계검토 B-2 의 요지대로
    평가자가 초안에서 논지를 역으로 읽게 하고, 계획과의 대조는 코드가 한다."""
    numbered = '\n'.join(f's{i}: {s}' for i, s in enumerate(split_sentences(draft), 1))
    exp_block = '\n'.join(f'--- {fn} ---\n{text}' for fn, text in allowed_experiences)
    parts = [f'[문항]\n{question}', f'[초안 — 문장 ID 가 붙어 있습니다]\n{numbered}']
    if exp_block:
        parts.append('[이 글이 쓸 수 있는 유일한 사실 출처 — fact_ids 에는 아래 파일명을 씁니다]\n'
                      + exp_block)
    if company_text.strip():
        parts.append('[확인된 기업 자료]\n' + company_text.strip())
    if job_text.strip():
        parts.append('[지원 공고]\n' + job_text.strip())
    parts.append('위 형식대로 ===REVIEW=== 블록만 돌려주세요.')
    return '\n\n'.join(parts)


def parse_review(text: str, expected_ids, allowed_fact_ids=()) -> Tuple[Optional[dict], Optional[str]]:
    """(검토 결과, 실패 사유). 문장 ID 가 우리가 보낸 집합과 어긋나면 실패로 본다 —
    엉뚱한 문장을 가리키는 지적은 고칠 수가 없다 (설계검토 B-2).

    S11-14: 문단 구분(paragraphs), 문장이 지지하는 주장(supports), 근거 출처(fact_ids)까지
    검증한다. fact_ids 는 설계검토의 "E01.A2" 같은 활동 단위가 아니라 **경험 파일명**이다 —
    이 레포의 경험 DB 에 활동 단위 ID 가 없어서다. 파일명 수준에서만 대조할 수 있다.
    """
    import json as _json
    m = _REVIEW_BLOCK.search(text or '')
    if not m:
        return None, 'REVIEW 블록 없음'
    try:
        data = _json.loads(m.group('body'))
    except ValueError as e:
        return None, f'JSON 파싱 실패: {e}'
    if not isinstance(data, dict):
        return None, 'JSON 최상위가 객체가 아님'

    expected = set(expected_ids)
    got = {s.get('id') for s in data.get('sentences', []) if isinstance(s, dict)}
    if got != expected:
        missing = sorted(expected - got)
        extra = sorted(got - expected)
        return None, f'문장 ID 불일치 (누락 {missing}, 없는 ID {extra})'

    paragraph_ids = {p.get('id') for p in data.get('paragraphs', []) if isinstance(p, dict)}
    allowed_facts = set(allowed_fact_ids)
    for s in data.get('sentences', []):
        supports = s.get('supports')
        if supports and paragraph_ids and supports not in paragraph_ids:
            return None, f'없는 문단을 가리킴: {supports}'
        unknown_facts = set(s.get('fact_ids') or []) - allowed_facts
        if allowed_facts and unknown_facts:
            return None, f'제공하지 않은 근거를 인용함: {sorted(unknown_facts)}'

    for issue in data.get('issues', []):
        if not isinstance(issue, dict):
            return None, 'issues 항목이 객체가 아님'
        if set(issue.get('sentence_ids') or []) - expected:
            return None, '지적이 없는 문장 ID 를 가리킴'
    return data, None


def blocking_issues(review: dict) -> List[dict]:
    return [i for i in review.get('issues', []) if i.get('kind') in BLOCKING_REVIEW_KINDS]


def build_repair_prompt(review: dict, draft: str, allowed_filenames) -> str:
    """"더 잘 써라" 가 아니라 문제 문장 ID, 결함 유형, 유지할 사실, 허용 범위를 준다
    (설계검토 B-3)."""
    sentences = {f's{i}': s for i, s in enumerate(split_sentences(draft), 1)}
    lines = []
    for issue in blocking_issues(review):
        ids = issue.get('sentence_ids') or []
        quoted = ' / '.join(sentences.get(i, '') for i in ids)
        lines.append(f'- [{issue.get("kind")}] {", ".join(ids)}: {issue.get("reason", "")}\n'
                      f'  해당 문장: {quoted}')
    return f"""검토 결과 아래 문제가 확인됐습니다. **지적된 부분만** 고쳐 주세요.

{chr(10).join(lines)}

지킬 것:
- 쓸 수 있는 경험은 그대로입니다: {', '.join(allowed_filenames)}. 새 경험이나 새 사실을 넣지 마세요.
- 지적되지 않은 문장은 건드리지 마세요.
- 근거가 없어서 주장을 받칠 수 없다면, 형용사를 늘리지 말고 그 주장을 빼세요.
- 글자수 조건과 문체 규칙은 그대로 유지합니다.

같은 형식(===PLAN=== / ===ANSWER=== / ===META=== / ===EVIDENCE===)으로 전체를 다시 돌려주세요.
"""


@dataclass
class CoverletterContext:
    """프롬프트에 실리는 모든 자료. build_prompt 가 위치 인자 14개를 받던 것을 이걸로 묶었다.

    S11-8: job_text 가 새로 들어왔다. 예전엔 공고 레코드를 통째로 들고 있으면서도
    회사명과 직무명 두 문자열만 생성에 넘겼다 — 프롬프트가 직무 적합성을 요구하면서
    정작 그 직무의 실제 요구조건을 주지 않았다(검토 0.3).
    """
    profile_text: str = ''
    style_text: str = ''
    rules_text: str = ''
    interest_text: str = ''
    company_text: str = ''
    reference_texts: List[Tuple[str, str]] = field(default_factory=list)
    trends_text: str = ''
    job_text: str = ''
    # S11-18: 공고의 필수·우대조건 원문. 여기서 대조할 도구 이름을 뽑는다.
    job_requirements: List[str] = field(default_factory=list)
    glossary: 'Glossary' = field(default_factory=lambda: Glossary())


MAX_REFERENCES = 3  # 레퍼런스는 프롬프트 비용이 커서 상한을 둔다


def _job_requirements(job) -> List[str]:
    """공고의 필수·우대조건을 리스트로 뽑는다. jd_terms 가 여기서 도구 이름을 찾는다."""
    if not job:
        return []
    import json as _json
    out = []
    for key in ('required_json', 'preferred_json'):
        raw = job.get(key)
        if not raw:
            continue
        try:
            items = _json.loads(raw) if isinstance(raw, str) else list(raw)
        except (ValueError, TypeError):
            continue
        out.extend(str(i) for i in items if i)
    return out


def _job_to_text(job: dict) -> str:
    """공고 레코드에서 자소서에 필요한 부분만 뽑는다. jobs 테이블 전체를 넣으면
    id, content_hash 같은 내부 값까지 프롬프트에 섞인다."""
    if not job:
        return ''
    import json as _json

    def arr(key):
        raw = job.get(key)
        if not raw:
            return []
        try:
            return _json.loads(raw) if isinstance(raw, str) else list(raw)
        except (ValueError, TypeError):
            return []

    parts = []
    for label, key in (('기업', 'company'), ('직무', 'position'), ('고용형태', 'employment_type'),
                       ('근무지', 'location'), ('경력', 'experience_level')):
        if job.get(key):
            parts.append(f'{label}: {job[key]}')
    for label, key in (('필수조건', 'required_json'), ('우대조건', 'preferred_json')):
        items = arr(key)
        if items:
            parts.append(f'{label}:\n' + '\n'.join(f'  - {i}' for i in items))
    if job.get('raw_text'):
        parts.append(f'공고 원문:\n{job["raw_text"]}')
    return '\n'.join(parts)


def gather_context(user_root: Path, *, companies_dir: Path, references_dir: Path,
                   company: str, position: str, trends_base: Optional[Path] = None,
                   use_trends: bool = True, job: Optional[dict] = None) -> CoverletterContext:
    """프롬프트 재료를 모은다. 파일을 읽기만 하고 판단하지 않는다 —
    기업 자료가 비었는지 같은 게이트 판정은 generate_coverletter_events 가 한다.

    references_dir 은 직무별 폴더의 **부모**다 (references/coverletter).
    trends_base 는 트렌드 저장소 디렉터리이고, 승인본만 읽힌다 (S11-6).
    """
    ref_dir = references_dir / position
    reference_texts = []
    if ref_dir.exists():
        # 파일명 정렬 후 앞 N 개다. 관련도 순이 아니라는 한계를 알고 쓴다 — 지금은
        # references/coverletter/ 가 비어 있어 실질 영향이 없고, 관련도 기반 선택은
        # 레퍼런스가 실제로 쌓인 뒤에 설계한다(설계검토 E-2).
        for f in sorted(ref_dir.glob('*.md'))[:MAX_REFERENCES]:
            reference_texts.append((f.name, f.read_text(encoding='utf-8')))

    trends_text = ''
    if use_trends and trends_base is not None:
        trends_text = trends_module.load_trends(trends_base) or ''

    return CoverletterContext(
        profile_text=read_if_exists(user_root / 'profile.md'),
        style_text=read_if_exists(user_root / 'writing-style.md'),
        rules_text=read_if_exists(user_root / 'writing-rules.md'),
        interest_text=read_if_exists(user_root / '관심직무.md'),
        company_text=read_if_exists(companies_dir / f'{company}.md'),
        reference_texts=reference_texts,
        trends_text=trends_text,
        job_text=_job_to_text(job) if job else '',
        job_requirements=_job_requirements(job),
        glossary=load_glossary(user_root),
    )


def generate_coverletter_events(user, question, exp_dir: Path, *,
                                 company: str, position: str,
                                 length: LengthSpec,
                                 context: 'CoverletterContext',
                                 override_filenames=None,
                                 max_experiences: int = MAX_EXPERIENCES_BY_CONTRACT,
                                 allow_unresearched_company: bool = False,
                                 output_dir: Optional[Path] = None,
                                 folder_name: Optional[str] = None,
                                 db_path=None,
                                 quality_check: bool = True,
                                 surface: str = 'script',
                                 complete=None):
    """자소서 한 문항을 생성하고 (event, data) 튜플을 yield 하는 제너레이터.

    S11-7 에서 web/routes/generate.py 밖으로 꺼냈다. 생성 엔진이 Flask 라우트 안에 있으면
    웹 외의 호출자가 쓸 수 없고(current_app 없이는 import 조차 안 된다), 테스트도 반드시
    HTTP 를 거쳐야 한다. lib/resume.generate_resume, lib/portfolio.generate_portfolio 는
    원래부터 Flask 비의존인데 자소서만 예외였다.

    경로는 전부 호출자가 해결해서 Path 로 넘긴다 — 라우트가 web.paths 로 안전성 검사를
    끝낸 값을 주는 구조다(lib/resume.py 와 같다).

    output_dir=None 이면 파일을 쓰지 않고 done 이벤트만 돌려준다 (generate_resume 와 동일 규약).
    complete=None 이면 lib.llm.stream_complete 를 쓴다 — 테스트가 주입할 수 있게 열어둔다.
    """
    issues = []  # S11-4: 저장은 하되 ready 로 올리면 안 되는 사유를 모은다
    ctx = context
    complete = complete or stream_complete
    name = user

    if not (exp_dir.exists() and any(exp_dir.glob('*.md'))):
        yield 'error', {'message': '경험 파일이 없습니다. 먼저 파싱을 진행하세요.'}
        return

    company_text = ctx.company_text

    # S11-1: 기업 자료 게이트. 예전에는 캐시가 없어도 company_cached: False 를 progress 로
    # 흘릴 뿐 아무도 막지 않았고, 프롬프트가 오히려 "학습 데이터로 추론하라" 고 지시했다.
    # 이 레포에는 companies/*.md 를 **쓰는 코드 경로가 없어서**(수작업 파일이다) 캐시 미스가
    # 기본값에 가깝다 — 그래서 조용한 열화가 아니라 멈춤이어야 한다.
    # S11-11 / 설계검토 A-4: "파일이 있다" 보다 강한 조건을 본다. 예전엔 strip() 한 줄이라
    # 출처 없는 요약, 다른 기업 내용, 2년 전 자료가 전부 "조사 완료" 로 통과했다.
    evidence_problems = check_company_evidence(company, company_text)
    if {'empty', 'mismatch'} & set(evidence_problems):
        reason = ('가 없습니다' if 'empty' in evidence_problems
                  else '의 내용이 이 기업 자료로 보이지 않습니다 (기업명이 본문에 없습니다)')
        if not allow_unresearched_company:
            yield 'error', {
                'message': f'companies/{company}.md {reason}. 기업 자료 없이 쓰면 '
                           f'사실관계를 지어낼 위험이 큽니다. 기업 조사를 먼저 하거나, '
                           f'공고 내용만으로 작성하는 데 동의해 주세요.',
                'needs_input': 'company_research',
                'company': company,
                'evidence_problems': evidence_problems,
            }
            return
        # 다른 기업 자료라면 넘기지 않는 편이 낫다. 틀린 사실을 주느니 없는 게 낫다.
        # ctx 를 통째로 바꿔야 한다 — build_prompt 와 검토 입력이 ctx.company_text 를 읽는다
        # (로컬 변수만 비웠다가 틀린 자료가 그대로 프롬프트에 실린 적이 있다).
        company_text = ''
        ctx = dataclass_replace(ctx, company_text='')
        issues.append(ISSUE_UNRESEARCHED_COMPANY)
        yield 'progress', {'stage': 'unresearched_company', 'company': company,
                            'evidence_problems': evidence_problems}
    else:
        # 막지는 않는다. 오래됐다고 못 쓰게 하면 오히려 추론을 유도한다 (설계검토 A-4).
        if {'stale', 'undated'} & set(evidence_problems):
            issues.append(ISSUE_COMPANY_STALE)
        if 'no_source' in evidence_problems:
            issues.append(ISSUE_COMPANY_THIN)
        if evidence_problems:
            yield 'progress', {'stage': 'company_evidence_weak',
                                'problems': evidence_problems,
                                'max_age_days': COMPANY_EVIDENCE_MAX_AGE_DAYS}

    # Step 5~7: 경험 선택은 LLM 이 아니라 코드가 한다 (S3-1). 선택된 것만 프롬프트에 실린다.
    selection = select_experiences(exp_dir, question, interest_text=ctx.interest_text,
                                    max_count=max_experiences,
                                    override_filenames=override_filenames)

    # S11-2: selector 는 보고만 한다. 중단 여부는 여기서 정한다.
    # UI 가 체크박스를 2개로 묶어도 API 를 직접 부르면 그냥 들어왔다 — 서비스 쪽에서 막아야 한다.
    if selection['unknown_overrides']:
        yield 'error', {
            'message': '지정한 경험 파일을 찾을 수 없습니다: '
                       + ', '.join(selection['unknown_overrides']),
            'unknown_overrides': selection['unknown_overrides'],
        }
        return
    if not selection['selected']:
        yield 'error', {'message': '사용할 경험이 하나도 선택되지 않았습니다. 경험을 선택한 뒤 다시 시도하세요.'}
        return
    if selection['duplicate_overrides']:
        issues.append(ISSUE_DUPLICATE_OVERRIDE)
        yield 'progress', {'stage': 'duplicate_overrides',
                            'duplicates': selection['duplicate_overrides']}
    if selection['dropped_overrides']:
        # 자르고 계속한다. 사용자가 고른 것을 통째로 버리는 것보다 낫지만, 고른 대로
        # 나오지 않았으니 사람이 확인해야 한다.
        issues.append(ISSUE_DROPPED_OVERRIDES)
        yield 'progress', {'stage': 'overrides_trimmed',
                            'kept': [c.experience.filename for c in selection['selected']],
                            'dropped': selection['dropped_overrides'],
                            'max': max_experiences}

    selected_experiences = [(c.experience.filename, c.experience.path.read_text(encoding='utf-8'))
                             for c in selection['selected']]
    yield 'progress', {
        'stage': 'selected',
        'question_type': selection['question_type'],
        'selected': [{'filename': c.experience.filename, 'title': c.experience.title,
                      'score': c.score, 'reasons': c.reasons} for c in selection['selected']],
        'candidates': [{'filename': c.experience.filename, 'title': c.experience.title,
                        'score': c.score, 'reasons': c.reasons} for c in selection['candidates'][:6]],
    }

    yield 'progress', {'stage': 'context_ready', 'experiences': len(selected_experiences),
                        'company_cached': bool(company_text.strip()),
                        'references': len(ctx.reference_texts)}

    system = load_skill('write-coverletter')
    user_msg = build_prompt(name, company, position, question, length, ctx, selected_experiences,
                             want_plan=quality_check)
    messages = [{'role': 'user', 'content': user_msg}]

    def call_llm(msgs, system=system):
        """system 기본값은 작성 스킬이다. 검토 호출만 다른 스킬을 넘긴다 —
        **생성 대화와 분리된 호출**이어야 하므로 messages 도 새로 만든다 (설계검토 B-2)."""
        q = queue.Queue()

        def worker():
            try:
                result = complete(system=system, messages=msgs, max_tokens=3000,
                                   on_delta=lambda c: q.put(('delta', c)))
                q.put(('done', result))
            except Exception as e:  # noqa: BLE001
                q.put(('error', str(e)))

        threading.Thread(target=worker, daemon=True).start()
        chars_since_last = 0
        while True:
            kind, payload = q.get()
            if kind == 'delta':
                chars_since_last += len(payload)
                if chars_since_last >= 200:
                    yield 'progress', {'stage': 'generating'}
                    chars_since_last = 0
                continue
            if kind == 'error':
                raise RuntimeError(payload)
            return payload

    try:
        result = yield from call_llm(messages)
    except RuntimeError as e:
        yield 'error', {'message': f'LLM 호출 실패: {e}'}
        return

    answer, meta = parse_answer(result['text'])
    actual = count_chars(answer, length.count_mode)
    yield 'progress', {'stage': 'draft', 'chars': actual, 'limit': length.hard_max_chars,
                        'length_mode': length.mode}

    # exceeded 는 "초과했다" 라는 뜻 그대로 유지한다 (파일 경고, 완료 이벤트, ROADMAP 완료 조건이
    # 전부 이 이름으로 추적한다). 재시도를 시킬지 여부(needs_retry)는 이보다 넓다 — 스킬 Step 9 가
    # "여유 있게 짧게 쓰는 것 금지" 라고 못박았으므로, 많이 모자란 경우도 다시 시킨다.
    retries = 0
    exceeded = False
    needs_retry = False
    if length.bounded:
        # S11-3: 제한이 없으면 "초과" 도 "모자람" 도 정의되지 않는다. 재시도 루프를 통째로 건넌다.
        exceeded = actual > length.hard_max_chars
        needs_retry = exceeded or abs(actual - length.hard_max_chars) / length.hard_max_chars > LENGTH_TOLERANCE
    while needs_retry and retries < MAX_RETRIES:
        retries += 1
        yield 'progress', {'stage': 'retry', 'attempt': retries, 'diff': actual - length.hard_max_chars}
        messages = messages + [
            {'role': 'assistant', 'content': result['text']},
            {'role': 'user', 'content': build_retry_prompt(length, actual, company)},
        ]
        try:
            result = yield from call_llm(messages)
        except RuntimeError as e:
            yield 'error', {'message': f'재시도 중 LLM 호출 실패: {e}'}
            return
        answer, meta = parse_answer(result['text'])
        actual = count_chars(answer, length.count_mode)
        exceeded = actual > length.hard_max_chars
        needs_retry = exceeded or abs(actual - length.hard_max_chars) / length.hard_max_chars > LENGTH_TOLERANCE

    if not answer:
        yield 'error', {'message': 'LLM 출력에서 ANSWER 블록을 찾지 못했습니다.', 'raw': result['text'][:500]}
        return

    supplied_filenames = [fn for fn, _ in selected_experiences]
    context_issues = list(issues)   # 기업 미조사, override 잘림 등 — 다시 쓴다고 사라지지 않는다

    def deterministic_check(raw_text, answer_text, meta_text):
        """모델 호출 없이 판정할 수 있는 것 전부. 수정 뒤에도 그대로 다시 돌린다 —
        평가 점수만 좋아지고 글자수나 사실이 틀어진 수정본으로 바꾸지 않기 위해서다."""
        found = []
        n = count_chars(answer_text, length.count_mode)
        over = False
        if length.bounded:
            over = n > length.hard_max_chars
            if over:
                found.append(ISSUE_OVER_LIMIT)
            elif abs(n - length.hard_max_chars) / length.hard_max_chars > LENGTH_TOLERANCE:
                found.append(ISSUE_UNDERSHOOT)
        # S11-10: 논지 계획 린트 (모델 호출 0회 — 같은 응답에 함께 왔다)
        plan_rows = parse_plan(raw_text)
        if quality_check:
            found.extend(lint_plan(plan_rows, supplied_filenames))
        # S11-9: 모델이 "참조한 경험" 으로 적은 파일명이 실제로 준 파일인지 대조
        cited_, unknown_ = validate_cited_experiences(meta_text, supplied_filenames)
        if unknown_:
            found.append(ISSUE_CITED_UNKNOWN)

        # S11-11: 초안에 나온 수치·기관명이 어떤 출처에도 없으면 알린다. **경보지
        # 허위 판정이 아니다** — 단위 환산, 반올림, 파생 수치는 원문에 그대로 없을 수 있다.
        # S11-18: 출처를 유형별로 넘긴다. 예전엔 전부 한 덩어리였고, 그래서 공고에
        # 적힌 수치가 본인 성과 주장까지 통과시켰다 (2차 설계검토 3-1).
        sources = [Source('experience', txt) for _, txt in selected_experiences]
        sources += [Source('company', ctx.company_text), Source('job', ctx.job_text),
                    Source('question', question)]
        # S11-12 와 맞물린 오탐 방지: glossary 가 "Hanze University of Applied Sciences
        # → 한제대학교" 처럼 풀어 쓰라고 지시한 표현은 경험 원문에 그대로 없다. 사전에
        # 등록된 설명형 표현과 허용 이름은 검증된 것으로 본다.
        # (실제 자소서를 쓰다 '한제대학교' 가 미확인 기관으로 잡혀서 발견했다.)
        # glossary 는 **이름 표기만** 증명한다 — 그 활동을 했다는 근거가 아니므로
        # experience 가 아니라 glossary 유형으로 넣는다.
        sources += [Source('glossary', g)
                    for g in list(ctx.glossary.explain.values()) + list(ctx.glossary.allowed)]
        nums, orgs = find_unverified_facts(answer_text, sources)
        if nums:
            found.append(ISSUE_UNKNOWN_NUMBER)
        if orgs:
            found.append(ISSUE_UNKNOWN_ORG)

        # S11-18: 어딘가에는 있지만 **본인 경험에는 없는** 사실
        jd_only = find_jd_only_facts(answer_text, sources)
        if jd_only:
            found.append(ISSUE_FACT_FROM_JD_ONLY)
        # S11-18: 공고에 적힌 도구를 써 봤다고 말하는데 경험 파일에 근거가 없는 경우
        unsupported_terms = check_jd_claims(
            answer_text, jd_terms(ctx.job_requirements),
            [txt for _, txt in selected_experiences])
        if unsupported_terms:
            found.append(ISSUE_JD_TERM_UNSUPPORTED)

        # S11-19: 주장마다 받은 인용 구절이 경험 원문에 실제로 있는지 코드가 대조한다.
        exp_by_name = dict(selected_experiences)
        ev_rows = parse_evidence(raw_text)
        if quality_check and EVIDENCE_REQUIRED:
            found.extend(lint_evidence(ev_rows, answer_text, exp_by_name))
        ev_report = evidence_report(ev_rows, exp_by_name)

        # S11-11: 코드로 판정 가능한 개인 규칙(현재는 가운뎃점 금지). 예전엔
        # writing-rules.md 에 적혀 있어도 프롬프트에만 맡겨져 있었다.
        broken = check_personal_rules(answer_text, ctx.rules_text)
        if broken:
            found.append(ISSUE_FORBIDDEN_CHAR)

        # S11-12: 설명 없이 쓰인 내부 고유명사
        unexplained = check_obscure_names(answer_text, ctx.glossary)
        if unexplained:
            found.append(ISSUE_UNEXPLAINED_NAME)
        return (found, n, over, plan_rows, cited_, unknown_, nums, orgs, broken,
                unexplained, jd_only, unsupported_terms, ev_report)

    (det, actual, exceeded, plan, cited_experiences, unknown_cited,
     unverified_numbers, unverified_orgs, broken_rules, unexplained_names,
     jd_only_facts, jd_terms_unsupported,
     evidence) = deterministic_check(result['text'], answer, meta)
    if jd_only_facts or jd_terms_unsupported:
        yield 'progress', {'stage': 'jd_scope', 'facts': jd_only_facts,
                            'terms': jd_terms_unsupported}
    if unexplained_names:
        yield 'progress', {'stage': 'unexplained_names', 'names': unexplained_names}
    if unverified_numbers or unverified_orgs:
        yield 'progress', {'stage': 'unverified_facts',
                            'numbers': unverified_numbers, 'organizations': unverified_orgs}
    if broken_rules:
        yield 'progress', {'stage': 'rule_violation', 'rules': broken_rules}
    if unknown_cited:
        yield 'progress', {'stage': 'cited_unknown', 'cited': cited_experiences,
                            'unknown': unknown_cited, 'supplied': supplied_filenames}
    if ISSUE_PLAN_MISSING not in det:
        yield 'progress', {'stage': 'plan', 'paragraphs': len(plan)}

    # S11-10: 별도 컨텍스트 검토 1회 + 지적 기반 수정 1회
    review, review_error, repairs = None, None, 0
    if quality_check:
        sentence_ids = [f's{i}' for i in range(1, len(split_sentences(answer)) + 1)]
        yield 'progress', {'stage': 'reviewing', 'sentences': len(sentence_ids)}
        review_input = build_review_input(
            question, answer, job_text=ctx.job_text, company_text=ctx.company_text,
            allowed_experiences=selected_experiences)
        try:
            review_raw = yield from call_llm([{'role': 'user', 'content': review_input}],
                                              system=load_skill('review-coverletter'))
            review, review_error = parse_review(review_raw['text'], sentence_ids,
                                                 allowed_fact_ids=supplied_filenames)
        except RuntimeError as e:
            review_error = f'검토 호출 실패: {e}'

        if review_error:
            # 검토가 실패해도 초안을 버리지 않는다. 검사하지 못했다는 사실만 남긴다.
            det.append(ISSUE_REVIEW_FAILED)
            yield 'progress', {'stage': 'review_failed', 'error': review_error}
        else:
            blocking = blocking_issues(review)
            yield 'progress', {'stage': 'reviewed', 'issues': len(review.get('issues', [])),
                                'blocking': len(blocking),
                                'inferred_claim': review.get('inferred_claim', '')}
            if blocking and repairs < MAX_REPAIRS:
                repairs += 1
                yield 'progress', {'stage': 'repair', 'attempt': repairs}
                messages = messages + [
                    {'role': 'assistant', 'content': result['text']},
                    {'role': 'user', 'content': build_repair_prompt(review, answer, supplied_filenames)},
                ]
                try:
                    repaired = yield from call_llm(messages)
                except RuntimeError as e:
                    yield 'progress', {'stage': 'repair_failed', 'error': str(e)}
                else:
                    new_answer, new_meta = parse_answer(repaired['text'])
                    if new_answer:
                        (new_det, n2, over2, plan2, cited2, unknown2,
                         nums2, orgs2, broken2, unexplained2,
                         jd2, terms2, ev2) = deterministic_check(
                            repaired['text'], new_answer, new_meta)
                        # 검토 지적을 고치면서 글자수나 사실이 틀어졌다면 수정본을 버린다.
                        worse = set(new_det) - set(det)
                        if worse:
                            yield 'progress', {'stage': 'repair_rejected',
                                                'new_issues': sorted(worse)}
                        else:
                            result, answer, meta = repaired, new_answer, new_meta
                            det, actual, exceeded = new_det, n2, over2
                            plan, cited_experiences, unknown_cited = plan2, cited2, unknown2
                            unverified_numbers, unverified_orgs = nums2, orgs2
                            broken_rules, unexplained_names = broken2, unexplained2
                            jd_only_facts, jd_terms_unsupported = jd2, terms2
                            evidence = ev2
                    else:
                        yield 'progress', {'stage': 'repair_failed', 'error': 'ANSWER 블록 없음'}
            # 수정 후에도(또는 수정 없이) 남은 차단성 지적은 사람에게 넘긴다.
            for issue in blocking_issues(review) if repairs == 0 else []:
                kind = issue.get('kind')
                if kind and kind not in det:
                    det.append(kind)

    issues = context_issues + [i for i in det if i not in context_issues]

    # S7 부터 이력서(lib/resume.py)·포트폴리오(lib/portfolio.py)가 outputs/[이름]/[기업]_[직무]/
    # 서브폴더에 저장하기 시작했다. 자소서만 플랫 파일로 따로 두면 S7-3(일괄 생성)에서 세 문서가
    # 한 폴더에 모이지 않으니, 여기도 같은 서브폴더 구조로 맞춘다.
    # S11-7: web.paths.safe_path 는 flask.abort(400) 을 부르므로 여기로 못 가져온다.
    # lib/resume.py 와 같은 방식으로 막는다 — 폴더명을 정리한 뒤 output_dir 밖으로
    # 나가지 않는지 확인하고, 실패하면 예외가 아니라 error 이벤트로 알린다.
    # (1차 방어선인 valid_segment 검사는 라우트에 그대로 남아 있다.)
    if output_dir is None:
        # 저장하지 않는 호출(미리보기, 테스트). done 은 그대로 준다.
        yield 'done', {
            'answer': answer, 'meta': meta, 'chars': actual,
            'limit': length.hard_max_chars, 'length_mode': length.mode,
            'count_mode': length.count_mode, 'exceeded': exceeded, 'retries': retries,
            'status': STATUS_REVIEW_REQUIRED if issues else STATUS_READY,
            'issues': issues, 'saved_to': None, 'rel_path': None,
            'used_experiences': supplied_filenames,
            'cited_experiences': cited_experiences,
            'plan': plan, 'review': review, 'review_error': review_error, 'repairs': repairs,
            'jd_only_facts': jd_only_facts, 'jd_terms_unsupported': jd_terms_unsupported,
            'evidence': evidence,
            'quality_checked': bool(quality_check and review is not None and not review_error),
            'run_id': None,   # 저장하지 않는 호출이라 실행 기록도 남기지 않는다
        }
        return

    folder = folder_name or _safe_folder_name(company, position)
    out_dir = output_dir.resolve()
    target_dir = (out_dir / folder).resolve()
    if not target_dir.is_relative_to(out_dir):
        yield 'error', {'message': '회사명/직무명에서 안전한 폴더명을 만들 수 없습니다.'}
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / '자소서.md'
    today = __import__('datetime').date.today().strftime('%Y.%m.%d')
    exceeded_note = (f'\n⚠️ 글자수 초과: {actual}자 (제한 {length.hard_max_chars}자, '
                     f'{actual - length.hard_max_chars}자 초과, 재시도 {retries}회 후에도 초과)\n') if exceeded else ''
    length_desc = (f'목표 {length.hard_max_chars}자' if length.bounded else '길이 제한 없음')
    # S11-1: 열화 모드로 쓴 글은 파일에도 흔적을 남긴다. 나중에 이 파일만 열어본 사람이
    # 기업 근거가 없는 초안이라는 걸 알 수 있어야 한다.
    unresearched_note = (f'\n⚠️ 기업 정보 미조사: companies/{company}.md 없이 사용자 동의 하에 작성됨. '
                         f'기업 관련 서술을 반드시 직접 확인하세요.\n'
                         if ISSUE_UNRESEARCHED_COMPANY in issues else '')
    entry = f"""## {question}
글자수: {actual}자 ({length.count_desc}, {length_desc}){exceeded_note}{unresearched_note}

{answer}

---
{meta}
작성일: {today}
"""
    # 자소서는 문항별 append 구조다. 전문을 만들어 넘겨야 버전이 "그 시점의 문서 전체" 가 된다.
    if out_path.exists():
        full_document = out_path.read_text(encoding='utf-8').rstrip() + '\n\n' + entry
    else:
        full_document = f'# {company} {position}\n\n{entry}'

    rel_path = str(out_path.relative_to(out_dir)).replace('\\', '/')

    # S11-9: 생성 초안도 버전으로 남긴다. 예전엔 lib/versions.save_version 이
    # web/routes/documents.py 의 수동 편집 저장에서만 불려서, 사람이 한 번 고치기 전까지
    # 이력이 없었다. 설계검토 E-3 의 초안↔최종본 diff 수집은 이 기록이 전제다.
    # S11-17: 이 초안이 어느 실행에서 나왔는지 함께 남긴다. documents.run_id 가 비어 있으면
    # 공통 실행기를 거치지 않고 저장된 문서라는 뜻이다 (2차 설계검토 4-1).
    quality_checked = bool(quality_check and review is not None and not review_error)
    run_id = runs.new_run_id()
    try:
        runs.record(runs.RunRecord(
            run_id=run_id, user=name, surface=surface, workflow='coverletter.generate',
            company=company, position=position, question=question,
            content_hash=runs.content_hash(answer),
            status=STATUS_REVIEW_REQUIRED if issues else STATUS_READY,
            issues=list(issues), quality_check=bool(quality_check), reviewed=quality_checked,
            inputs={'experiences': list(supplied_filenames), 'retries': retries,
                    'repairs': repairs, 'length_mode': length.mode,
                    'evidence_quotes_found': sum(1 for e in evidence
                                                  if e['verdict'] == 'quote_found'),
                    'evidence_rows': len(evidence)},
        ), db_path=db_path)
    except Exception as e:  # noqa: BLE001
        yield 'progress', {'stage': 'run_record_failed', 'error': str(e)}

    version = None
    try:
        vr = versions.save_version(out_dir, rel_path, full_document, user=name,
                                   db_path=db_path, run_id=run_id)
        version = vr.version if vr.ok else None
    except Exception as e:  # noqa: BLE001
        # DB 문제로 **초안을 잃지는 않는다.** 파일은 직접 쓰고 기록 실패만 알린다.
        out_path.write_text(full_document, encoding='utf-8')
        yield 'progress', {'stage': 'version_save_failed', 'error': str(e)}
    yield 'done', {
        'answer': answer,
        'meta': meta,
        'chars': actual,
        'limit': length.hard_max_chars,
        'length_mode': length.mode,
        'count_mode': length.count_mode,
        'exceeded': exceeded,
        'retries': retries,
        # S11-4: 자동 검사 결과. ready_for_user 는 "검사를 통과했다" 이지
        # "사용자가 승인했다" 나 "제출했다" 가 아니다.
        'status': STATUS_REVIEW_REQUIRED if issues else STATUS_READY,
        'issues': issues,
        # saved_to 는 프로젝트 루트 기준 표시용이다. outputs/<user>/ 는 고정 규약이라
        # lib 가 project_root 를 몰라도 재구성할 수 있다 (화면은 rel_path 를 먼저 쓴다).
        'saved_to': f'outputs/{name}/{rel_path}',
        # S11-5: saved_to 는 프로젝트 루트 기준이라 화면에서 링크를 만들 때 쓰면
        # outputs/<user>/ 를 다시 떼어내야 한다(예전엔 파일명만 떼어 서브폴더를 잃었다).
        # rel_path 는 /output/<user>/<path:filename> 라우트에 그대로 붙일 수 있는 형태다.
        'rel_path': rel_path,
        'used_experiences': supplied_filenames,   # 코드가 고른 파일 (사실)
        # S11-9: 모델이 썼다고 보고한 파일 (자기보고). 둘을 나란히 두면 어긋남이 보인다.
        'cited_experiences': cited_experiences,
        'version': version,
        'full_document': full_document,
        # S11-10: 무엇을 어떻게 검사했는지 남긴다. 점수 하나로 통과/탈락을 가르지 않는다.
        'plan': plan,
        'unverified_numbers': unverified_numbers,
        'unverified_organizations': unverified_orgs,
        'broken_rules': broken_rules,
        'unexplained_names': unexplained_names,
        # S11-18: 어딘가에는 있지만 본인 경험에는 근거가 없는 것
        'jd_only_facts': jd_only_facts,
        'jd_terms_unsupported': jd_terms_unsupported,
        # S11-19: 주장별 근거 대조 결과. verdict 는 "그 인용이 원문에 있는가" 만 말한다.
        'evidence': evidence,
        'review': review,
        'review_error': review_error,
        'repairs': repairs,
        # S11-17: 검사 통과 / 검사 실패 / **검사하지 않음** 을 구분한다. 토글을 끄고 받은
        # 초안이 검사를 통과한 초안과 같은 얼굴로 보이면 안 된다 (2차 설계검토 4-3).
        'quality_checked': quality_checked,
        'run_id': run_id,
    }


def revise_coverletter_events(user, question: str, exp_dir: Path, *,
                               rel_path: str,
                               instruction: str,
                               length: LengthSpec,
                               context: 'CoverletterContext',
                               output_dir: Path,
                               db_path=None,
                               surface: str = 'script',
                               complete=None):
    """이미 저장된 자소서의 한 문항을 고치고 **문서 전체**를 돌려준다 (S11-13, 설계검토 C-3).

    불만 5번("수정하면 바뀐 부분만 보여준다")은 프롬프트로 당부할 문제가 아니라 출력
    계약 문제다. 모델은 그 문항의 답변만 돌려줘도 되고, 전체 문서는 서버가 재구성한다.
    고치지 않은 문항은 그대로 보존된다.

    허용 경험은 **원문에 기록된 것을 그대로 쓴다.** 수정하면서 새 경험이 끼어드는 것을
    막기 위해서다 — 재선택이 필요하면 수정이 아니라 새로 생성해야 한다.

    결정적 검사(글자수, 인용, 수치·기관명, 개인 규칙, 고유명사)는 생성과 동일하게 건다.
    별도 검토 호출(critic)은 걸지 않는다 — 사용자가 직접 지시한 수정이고, 호출을 한 번 더
    늘릴 만큼의 근거가 아직 없다. 필요해지면 generate 쪽 구조를 그대로 가져오면 된다.
    """
    complete = complete or stream_complete
    ctx = context
    issues = []

    out_dir = output_dir.resolve()
    doc_path = (out_dir / rel_path).resolve()
    if not doc_path.is_relative_to(out_dir) or not doc_path.exists():
        yield 'error', {'message': f'수정할 문서를 찾을 수 없습니다: {rel_path}'}
        return

    document = doc_path.read_text(encoding='utf-8')
    section = find_section(document, question)
    if section is None:
        available = [q for q, _ in split_document(document)[1]]
        yield 'error', {'message': f'그 문항을 문서에서 찾지 못했습니다: {question}',
                        'issues': [ISSUE_SECTION_NOT_FOUND], 'available_questions': available}
        return

    current_answer = section_answer(section)
    allowed = section_cited_files(section)
    # 원문에 기록이 없으면(옛 파일) 경험 디렉터리 전체를 허용 범위로 두지 않는다 —
    # 그러면 수정이 사실상 재생성이 된다. 파일명 기록이 없을 때만 제한을 비워 둔다.
    allowed_texts = [(fn, (exp_dir / fn).read_text(encoding='utf-8'))
                     for fn in allowed if (exp_dir / fn).exists()]

    yield 'progress', {'stage': 'revise_start', 'question': question,
                       'allowed_experiences': [fn for fn, _ in allowed_texts]}

    system = load_skill('write-coverletter')
    messages = [{'role': 'user',
                 'content': build_revise_prompt(question, current_answer, instruction,
                                                 length, allowed)}]

    def call_llm(msgs, system=system):
        q = queue.Queue()

        def worker():
            try:
                q.put(('done', complete(system=system, messages=msgs, max_tokens=3000,
                                         on_delta=lambda c: q.put(('delta', c)))))
            except Exception as e:  # noqa: BLE001
                q.put(('error', str(e)))

        threading.Thread(target=worker, daemon=True).start()
        seen = 0
        while True:
            kind, payload = q.get()
            if kind == 'delta':
                seen += len(payload)
                if seen >= 200:
                    yield 'progress', {'stage': 'generating'}
                    seen = 0
                continue
            if kind == 'error':
                raise RuntimeError(payload)
            return payload

    try:
        result = yield from call_llm(messages)
    except RuntimeError as e:
        yield 'error', {'message': f'LLM 호출 실패: {e}'}
        return

    answer, meta = parse_answer(result['text'])
    if not answer:
        yield 'error', {'message': 'LLM 출력에서 ANSWER 블록을 찾지 못했습니다.',
                        'raw': result['text'][:500]}
        return

    # 수정본에도 생성과 **같은 결정적 검사**를 건다. 지시를 따르느라 글자수나 사실이
    # 틀어지는 것을 막는다.
    actual = count_chars(answer, length.count_mode)
    exceeded = length.bounded and actual > length.hard_max_chars
    if exceeded:
        issues.append(ISSUE_OVER_LIMIT)
    supplied = [fn for fn, _ in allowed_texts]
    _, unknown_cited = validate_cited_experiences(meta, supplied) if supplied else ([], [])
    if unknown_cited:
        issues.append(ISSUE_CITED_UNKNOWN)
    # S11-18: 수정 경로도 생성과 같은 기준으로 출처를 유형별로 본다. 처음에 안전했던
    # 글이 수정하면서 공고 내용을 자기 경험처럼 끌어오는 것을 막는다 (2차 설계검토 4-2).
    rev_sources = [Source('experience', txt) for _, txt in allowed_texts]
    rev_sources += [Source('company', ctx.company_text), Source('job', ctx.job_text),
                    Source('question', question)]
    rev_sources += [Source('glossary', g)
                    for g in list(ctx.glossary.explain.values()) + list(ctx.glossary.allowed)]
    nums, orgs = find_unverified_facts(answer, rev_sources)
    if nums:
        issues.append(ISSUE_UNKNOWN_NUMBER)
    if orgs:
        issues.append(ISSUE_UNKNOWN_ORG)
    jd_only = find_jd_only_facts(answer, rev_sources)
    if jd_only:
        issues.append(ISSUE_FACT_FROM_JD_ONLY)
    jd_unsupported = check_jd_claims(answer, jd_terms(ctx.job_requirements),
                                      [txt for _, txt in allowed_texts])
    if jd_unsupported:
        issues.append(ISSUE_JD_TERM_UNSUPPORTED)
    broken = check_personal_rules(answer, ctx.rules_text)
    if broken:
        issues.append(ISSUE_FORBIDDEN_CHAR)
    unexplained = check_obscure_names(answer, ctx.glossary)
    if unexplained:
        issues.append(ISSUE_UNEXPLAINED_NAME)

    # 구간을 다시 조립한다. 머리말의 글자수 줄과 작성일을 갱신하고 나머지는 보존한다.
    today = date.today().strftime('%Y.%m.%d')
    length_desc = f'목표 {length.hard_max_chars}자' if length.bounded else '길이 제한 없음'
    exceeded_note = (f'\n⚠️ 글자수 초과: {actual}자 (제한 {length.hard_max_chars}자)\n'
                     if exceeded else '')
    new_section = (f'{question}\n글자수: {actual}자 ({length.count_desc}, {length_desc})'
                   f'{exceeded_note}\n\n{answer}\n\n---\n{meta}\n수정일: {today}\n')

    full_document = replace_section(document, question, new_section)
    # S11-17: 수정도 실행 기록을 남긴다. 수정 경로에는 의미 평가(critic)가 없으므로
    # reviewed=False 다 — 그 사실이 기록에 그대로 남아야 한다.
    run_id = runs.new_run_id()
    try:
        runs.record(runs.RunRecord(
            run_id=run_id, user=user, surface=surface, workflow='coverletter.revise',
            question=question, content_hash=runs.content_hash(answer),
            status=STATUS_REVIEW_REQUIRED if issues else STATUS_READY,
            issues=list(issues), quality_check=False, reviewed=False,
            inputs={'experiences': supplied, 'rel_path': rel_path},
        ), db_path=db_path)
    except Exception as e:  # noqa: BLE001
        yield 'progress', {'stage': 'run_record_failed', 'error': str(e)}
    vr = versions.save_version(out_dir, rel_path, full_document, user=user,
                               db_path=db_path, run_id=run_id)

    yield 'done', {
        'answer': answer,
        'meta': meta,
        'chars': actual,
        'limit': length.hard_max_chars,
        'length_mode': length.mode,
        'count_mode': length.count_mode,
        'exceeded': exceeded,
        'status': STATUS_REVIEW_REQUIRED if issues else STATUS_READY,
        'issues': issues,
        'unverified_numbers': nums,
        'unverified_organizations': orgs,
        'broken_rules': broken,
        'unexplained_names': unexplained,
        'jd_only_facts': jd_only,
        'jd_terms_unsupported': jd_unsupported,
        'used_experiences': supplied,
        # 설계검토 C-3: 외부 응답은 항상 전체 본문이다. 발췌만 돌려주지 않는다.
        'full_document': full_document,
        'rel_path': rel_path,
        'saved_to': f'outputs/{user}/{rel_path}',
        'version': vr.version if vr.ok else None,
        # 수정 경로는 결정적 검사만 돈다. 의미 평가는 돌지 않았다는 사실을 숨기지 않는다.
        'quality_checked': False,
        'run_id': run_id,
    }
