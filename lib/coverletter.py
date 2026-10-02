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
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

from lib import trends as trends_module
from lib.llm import stream_complete
from lib.resume import _safe_folder_name
from lib.selector import MAX_EXPERIENCES
from lib.selector import select as select_experiences
from lib.skills import load_skill


_ANSWER_BLOCK = re.compile(r'===ANSWER===\n(?P<body>.*?)\n===END===', re.S)
_META_BLOCK = re.compile(r'===META===\n(?P<body>.*?)\n===END===', re.S)

MAX_RETRIES = 1  # ROADMAP: 초과 시 재시도 1회

# 매직넘버를 한곳에 모은다 (S11-5). 각 값의 근거는 PROGRESS.md 의 S1-1~S1-3 실측 기록이다.
LENGTH_TOLERANCE = 0.05   # 목표 대비 이만큼 모자라면 다시 시킨다 (스킬 Step 9: 여유 있게 짧게 쓰기 금지)
TARGET_UNDERSHOOT = 20    # 재시도 목표를 상한에 붙이지 않고 이만큼 아래로 둔다 (미세 오차 대비)
MAX_CHAR_LIMIT = 10000    # 상한의 상한. 모델 max_tokens=3000 과 모순되는 값을 막는다

# S11-4: "저장했다" 와 "제출해도 된다" 를 분리한다. 이 둘을 같은 값으로 묶어두면
# (예전의 done → coverletter_ok=True) 글자수를 초과한 초안도 성공으로 보고된다.
# 저장은 그대로 한다 — 사람이 손봐서 쓸 수 있으니 버리지 않는다. 다만 ready 로 올리지 않는다.
STATUS_READY = 'ready_for_user'          # 자동 검사를 통과했다. 사용자 최종 승인이나 제출과는 다른 뜻이다
STATUS_REVIEW_REQUIRED = 'review_required'  # 저장은 됐지만 사람이 봐야 한다

ISSUE_OVER_LIMIT = 'over_limit'
ISSUE_UNDERSHOOT = 'undershoot'
ISSUE_DROPPED_OVERRIDES = 'dropped_overrides'
ISSUE_UNRESEARCHED_COMPANY = 'unresearched_company'


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
                  ctx: 'CoverletterContext', selected_experiences):
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
===ANSWER===
(작성된 문단. 이것만 글자수 계산에 사용되니 다른 텍스트를 섞지 마세요)
===END===

===META===
참조한 경험: [경험명] ([파일명])
참조한 기업 정보: [companies/{company}.md 또는 "없음 (추론)"]
참조한 레퍼런스: [파일명 또는 "없음"]
오래된 경험 사용 여부: [예/아니오]
===END===
"""


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


def parse_answer(text):
    a = _ANSWER_BLOCK.search(text)
    m = _META_BLOCK.search(text)
    answer = a.group('body').strip() if a else ''
    meta = m.group('body').strip() if m else ''
    return answer, meta


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


MAX_REFERENCES = 3  # 레퍼런스는 프롬프트 비용이 커서 상한을 둔다


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
    )


def generate_coverletter_events(user, question, exp_dir: Path, *,
                                 company: str, position: str,
                                 length: LengthSpec,
                                 context: 'CoverletterContext',
                                 override_filenames=None,
                                 max_experiences: int = MAX_EXPERIENCES,
                                 allow_unresearched_company: bool = False,
                                 output_dir: Optional[Path] = None,
                                 folder_name: Optional[str] = None,
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
    if not company_text.strip():
        if not allow_unresearched_company:
            yield 'error', {
                'message': f'companies/{company}.md 가 없습니다. 기업 자료 없이 쓰면 '
                           f'사실관계를 지어낼 위험이 큽니다. 기업 조사를 먼저 하거나, '
                           f'공고 내용만으로 작성하는 데 동의해 주세요.',
                'needs_input': 'company_research',
                'company': company,
            }
            return
        issues.append(ISSUE_UNRESEARCHED_COMPANY)
        yield 'progress', {'stage': 'unresearched_company', 'company': company}

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
    user_msg = build_prompt(name, company, position, question, length, ctx, selected_experiences)
    messages = [{'role': 'user', 'content': user_msg}]

    def call_llm(msgs):
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

    # S11-4: 재시도를 다 쓰고도 목표를 못 맞춘 경우. exceeded(초과)뿐 아니라 크게 모자란
    # 경우도 사람이 봐야 한다 — 스킬 Step 9 가 "여유 있게 짧게 쓰는 것 금지" 라고 못박았다.
    if exceeded:
        issues.append(ISSUE_OVER_LIMIT)
    elif needs_retry:
        issues.append(ISSUE_UNDERSHOOT)

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
            'used_experiences': [fn for fn, _ in selected_experiences],
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
    if out_path.exists():
        out_path.write_text(out_path.read_text(encoding='utf-8').rstrip() + '\n\n' + entry, encoding='utf-8')
    else:
        out_path.write_text(f'# {company} {position}\n\n{entry}', encoding='utf-8')

    rel_path = str(out_path.relative_to(out_dir)).replace('\\', '/')
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
        'used_experiences': [fn for fn, _ in selected_experiences],  # 코드가 실제로 고른 파일 (LLM 자체보고가 아니다)
    }
