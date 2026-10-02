"""생성 라우트. skills/write-coverletter.md 로 문항 하나에 대한 자소서 문단을 만든다 (ROADMAP S1-3).

이 라우트에서 가장 중요한 규칙은 하나다: **글자수는 LLM 이 아니라 코드가 잰다.**
S1-1 에서 모델이 자체 보고한 글자수(683자)가 실제 글자수(715자)와 32자 어긋나는 것을 실측으로
확인했다 (PROGRESS.md 참고). 그래서 여기서는 모델이 뭐라고 보고하든 무시하고, len() 으로 직접 센다.

원본 스킬(write-coverletter.md)의 자연어 Step 9 출력 형식은 사람이 읽기엔 좋지만 코드가 안정적으로
파싱하기엔 약하다. 그래서 S1-2 와 같은 방식으로, 우리가 정한 구분자 안에 본문만 담아 달라고 요청한다
(스킬 파일 자체는 건드리지 않는다 — 1-B 원칙. 구분자는 이 라우트의 사용자 메시지에서만 요구한다).

경험 선택(Step 7)은 lib/selector.py 가 코드로 한다 (ROADMAP S3-1). LLM 호출에는 선택된 1~2개
경험의 전문만 실린다 — 전체를 다 보내던 이전 방식보다 비용이 훨씬 적게 든다. 코드가 고른 결과가
마음에 안 들면 요청에 experience_files 를 넣어 강제로 지정할 수 있다 (S3-2 가 이 위에 UI 를 얹는다).
"""
import json
import queue
import re
import threading
from pathlib import Path

from flask import (Blueprint, Response, current_app, jsonify, redirect, render_template,
                   request, stream_with_context, url_for)

from lib import db, matcher, portfolio, resume, trends
from lib.llm import stream_complete
from lib.resume import _safe_folder_name
from lib.selector import MAX_EXPERIENCES
from lib.selector import select as select_experiences
from lib.skills import load_skill
from web.data import get_open_jobs
from web.paths import output_dir, project_root, safe_path, trends_path, user_dir, valid_segment


def _db_path():
    """S7-3(bundle)이 SQLite(jobs/matches)를 건드리는 첫 지점이다. web/routes/jobs.py,
    matches.py 와 같은 이유로 DB_PATH 설정을 존중한다 — 안 그러면 테스트가 실제 프로젝트
    DB(data/app.db)를 건드리게 된다."""
    path = current_app.config.get('DB_PATH')
    db.init_db(path)
    return path

bp = Blueprint('generate', __name__)


@bp.route('/generate/<name>', methods=['GET'])
def generate_page(name):
    if not user_dir(name).exists():
        return redirect(url_for('main.index'))
    exp_dir = user_dir(name) / 'experiences'
    exp_count = len(list(exp_dir.glob('*.md'))) if exp_dir.exists() else 0
    jobs = get_open_jobs(db_path=_db_path())  # S7-3: 공고에서 불러오기 드롭다운
    return render_template('generate.html', user=name, exp_count=exp_count, jobs=jobs)

_ANSWER_BLOCK = re.compile(r'===ANSWER===\n(?P<body>.*?)\n===END===', re.S)
_META_BLOCK = re.compile(r'===META===\n(?P<body>.*?)\n===END===', re.S)

MAX_RETRIES = 1  # ROADMAP: 초과 시 재시도 1회

# S11-4: "저장했다" 와 "제출해도 된다" 를 분리한다. 이 둘을 같은 값으로 묶어두면
# (예전의 done → coverletter_ok=True) 글자수를 초과한 초안도 성공으로 보고된다.
# 저장은 그대로 한다 — 사람이 손봐서 쓸 수 있으니 버리지 않는다. 다만 ready 로 올리지 않는다.
STATUS_READY = 'ready_for_user'          # 자동 검사를 통과했다. 사용자 최종 승인이나 제출과는 다른 뜻이다
STATUS_REVIEW_REQUIRED = 'review_required'  # 저장은 됐지만 사람이 봐야 한다

ISSUE_OVER_LIMIT = 'over_limit'
ISSUE_UNDERSHOOT = 'undershoot'
ISSUE_DROPPED_OVERRIDES = 'dropped_overrides'


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


def build_prompt(name, company, position, question, char_limit, count_mode,
                  profile_text, style_text, rules_text, interest_text,
                  company_text, reference_texts, selected_experiences, trends_text=''):
    exp_dump = '\n\n'.join(f'--- users/{name}/experiences/{fn} ---\n{text}' for fn, text in selected_experiences)
    ref_dump = ('\n\n'.join(f'--- {fn} ---\n{text}' for fn, text in reference_texts)
                if reference_texts else '(로컬 레퍼런스 없음. MCP 는 이 환경에 연결되어 있지 않으므로 Step 4 는 생략)')
    company_note = (
        f'--- companies/{company}.md ---\n{company_text}'
        if company_text.strip()
        else f'companies/{company}.md 캐시가 없습니다. MCP 가 연결되어 있지 않으므로 Step 2 지시대로 '
             '학습 데이터 기반 일반 정보만 참고하고, 과도하게 구체적인 최신 수치·뉴스는 인용하지 마세요.'
    )
    rules_note = strip_placeholder_rules(rules_text) or '(비어 있음 — 적용할 규칙 없음, 무시)'
    count_desc = '공백 포함' if count_mode == '포함' else '공백 제외'
    # 고도화(2026-09-27): lib/trends.py 가 한 달에 한 번 자동 조사해두는 최신 자소서 작성
    # 트렌드. 참고용일 뿐 writing-style.md/writing-rules.md 보다 우선하지 않는다 — Claude.md
    # 의 "반드시 해당 사용자의 writing-style.md 어투를 따를 것" 절대 원칙이 이 트렌드 참고보다
    # 위에 있다는 걸 프롬프트에도 명시한다(안 그러면 트렌드 문구를 어투보다 우선할 위험이 있다).
    trends_note = (
        trends_text.strip()
        if trends_text.strip()
        else '(아직 조사된 트렌드 없음 — 무시)'
    )

    return f"""[요청]
사용자: {name}
기업명: {company}
직무명: {position}
문항: {question}
글자수: {char_limit}자 ({count_desc})

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
- 글자수는 최대한 {char_limit}자에 가깝게 채우되 절대 넘기지 마세요 (여유를 약간 두는 편이 넘기는 것보다 낫습니다).
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


def build_retry_prompt(char_limit, count_mode, current_count, company=''):
    """초과했을 때뿐 아니라, 많이 모자랄 때도 부른다 (스킬 Step 9: "여유 있게 짧게 쓰는 것 금지").
    목표를 하나의 정확한 숫자로 주고, 그 숫자에 맞춰 문장 단위로 더하거나 빼라고 구체적으로 지시한다.
    "권장"처럼 약한 표현을 쓰면 모델이 충분히 맞추지 않는 경향이 실측에서 확인되어, 지시를 강하게 바꿨다.
    """
    count_desc = '공백 포함' if count_mode == '포함' else '공백 제외'
    diff = current_count - char_limit
    target = max(char_limit - 20, 1)  # 상한에 붙이지 않고 20자 정도 여유를 둔다 (미세 오차 대비)

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


def sse(event, data):
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


def _parse_use_trends(data):
    """폼(문자열 'true'/'false'/'on')과 JSON(bool) 양쪽에서 와도 되게 관대하게 받는다.
    안 보내면 기본값은 True(트렌드 반영) — 기존 동작(항상 반영)과 호환된다."""
    raw = data.get('use_trends', True)
    if isinstance(raw, str):
        return raw.strip().lower() not in ('false', '0', '')
    return bool(raw)


def _validate_generate_params(data):
    """company/position/question/char_limit 검증. (errors, company, position, question,
    char_limit, count_mode, override_files, use_trends) 를 돌려준다 — SSE 제너레이터가
    시작되기 *전에* 동기적으로 끝내야 한다 (S1-3 에서 겪은, 제너레이터 안에서 abort/에러 내면
    이미 200이 나간 뒤라 늦다는 패턴을 여기서도 피한다)."""
    company = (data.get('company') or '').strip()
    position = (data.get('position') or '').strip()
    question = (data.get('question') or '').strip()
    count_mode = (data.get('count_mode') or '포함').strip()
    if count_mode not in ('포함', '제외'):
        count_mode = '포함'

    errors = []
    if not company:
        errors.append('기업명이 없습니다.')
    elif not valid_segment(company):
        errors.append('기업명에 사용할 수 없는 문자가 있습니다. (/ \\ .. 등)')
    if not position:
        errors.append('직무명이 없습니다.')
    elif not valid_segment(position):
        errors.append('직무명에 사용할 수 없는 문자가 있습니다. (/ \\ .. 등)')
    if not question:
        errors.append('문항이 없습니다.')
    try:
        char_limit = int(data.get('char_limit'))
        if char_limit <= 0:
            raise ValueError
    except (TypeError, ValueError):
        char_limit = None
        errors.append('글자수(char_limit)가 없거나 올바르지 않습니다. 반드시 숫자로 지정해야 합니다.')

    override_files = data.get('experience_files')
    if isinstance(override_files, str):
        override_files = [f.strip() for f in override_files.split(',') if f.strip()]

    use_trends = _parse_use_trends(data)

    return errors, company, position, question, char_limit, count_mode, override_files, use_trends


def coverletter_events(name, root, company, position, question, char_limit, count_mode,
                        override_files=None, folder_name=None, use_trends=True):
    """자소서 한 문항을 생성해 저장하고, (event, data) 튜플을 yield 하는 순수 제너레이터.

    /generate/<name> (단독 생성)과 /generate/<name>/bundle (S7-3 공고 연동 일괄 생성) 양쪽에서
    그대로 재사용한다 — 두 라우트가 이 로직을 복제하면 언젠가 하나만 고치고 잊어버리게 된다.
    folder_name 을 주면 outputs/[이름]/[folder_name]/ 에 저장한다(lib/resume.py, lib/portfolio.py
    와 폴더를 통일하려는 것 — 공고 연동 시엔 회사명/직무명이 공고 레코드에서 오므로 그 값 그대로
    폴더명을 만들면 안전하지 않을 수 있어 호출자가 이미 정리한 폴더명을 넘긴다). 안 주면 기존처럼
    [기업명]_[직무명] 을 그대로 쓴다(이 경우 company/position 은 이미 valid_segment() 를 통과한
    값이어야 한다 — 단독 라우트가 그렇게 보장한다).

    root 는 이미 검증된 user_dir(name) 이다 — 이 함수 자체는 name 으로 다시 경로를 만들지 않는다
    (제너레이터 시작 전에 이미 끝났어야 할 검증을 여기서 반복하지 않기 위해).
    """
    issues = []  # S11-4: 저장은 하되 ready 로 올리면 안 되는 사유를 모은다

    exp_dir = root / 'experiences'
    if not (exp_dir.exists() and any(exp_dir.glob('*.md'))):
        yield 'error', {'message': '경험 파일이 없습니다. 먼저 파싱을 진행하세요.'}
        return

    profile_text = read_if_exists(root / 'profile.md')
    style_text = read_if_exists(root / 'writing-style.md')
    rules_text = read_if_exists(root / 'writing-rules.md')
    interest_text = read_if_exists(root / '관심직무.md')
    company_text = read_if_exists(project_root() / 'companies' / f'{company}.md')
    # use_trends=False 면 아예 빈 문자열로 넘긴다 — build_prompt 는 빈 값일 때 이미
    # "(아직 조사된 트렌드 없음 — 무시)" 로 처리하므로 별도 분기 없이 그대로 재사용한다.
    trends_text = (trends.load_trends(trends_path()) or '') if use_trends else ''

    ref_dir = project_root() / 'references' / 'coverletter' / position
    reference_texts = []
    if ref_dir.exists():
        for f in sorted(ref_dir.glob('*.md'))[:3]:
            reference_texts.append((f.name, f.read_text(encoding='utf-8')))

    # Step 5~7: 경험 선택은 LLM 이 아니라 코드가 한다 (S3-1). 선택된 것만 프롬프트에 실린다.
    selection = select_experiences(exp_dir, question, interest_text=interest_text,
                                    max_count=MAX_EXPERIENCES, override_filenames=override_files)

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
                            'max': MAX_EXPERIENCES}

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
                        'company_cached': bool(company_text.strip()), 'references': len(reference_texts)}

    system = load_skill('write-coverletter')
    user_msg = build_prompt(name, company, position, question, char_limit, count_mode,
                             profile_text, style_text, rules_text, interest_text,
                             company_text, reference_texts, selected_experiences, trends_text)
    messages = [{'role': 'user', 'content': user_msg}]

    def call_llm(msgs):
        q = queue.Queue()

        def worker():
            try:
                result = stream_complete(system=system, messages=msgs, max_tokens=3000,
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
    actual = count_chars(answer, count_mode)
    yield 'progress', {'stage': 'draft', 'chars': actual, 'limit': char_limit}

    # exceeded 는 "초과했다" 라는 뜻 그대로 유지한다 (파일 경고, 완료 이벤트, ROADMAP 완료 조건이
    # 전부 이 이름으로 추적한다). 재시도를 시킬지 여부(needs_retry)는 이보다 넓다 — 스킬 Step 9 가
    # "여유 있게 짧게 쓰는 것 금지" 라고 못박았으므로, 많이 모자란 경우도 다시 시킨다.
    retries = 0
    exceeded = actual > char_limit
    needs_retry = exceeded or abs(actual - char_limit) / char_limit > 0.05
    while needs_retry and retries < MAX_RETRIES:
        retries += 1
        yield 'progress', {'stage': 'retry', 'attempt': retries, 'diff': actual - char_limit}
        messages = messages + [
            {'role': 'assistant', 'content': result['text']},
            {'role': 'user', 'content': build_retry_prompt(char_limit, count_mode, actual, company)},
        ]
        try:
            result = yield from call_llm(messages)
        except RuntimeError as e:
            yield 'error', {'message': f'재시도 중 LLM 호출 실패: {e}'}
            return
        answer, meta = parse_answer(result['text'])
        actual = count_chars(answer, count_mode)
        exceeded = actual > char_limit
        needs_retry = exceeded or abs(actual - char_limit) / char_limit > 0.05

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
    out_dir = output_dir(name)
    out_path = safe_path(out_dir, folder_name or f'{company}_{position}', '자소서.md')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    today = __import__('datetime').date.today().strftime('%Y.%m.%d')
    exceeded_note = f'\n⚠️ 글자수 초과: {actual}자 (제한 {char_limit}자, {actual - char_limit}자 초과, 재시도 {retries}회 후에도 초과)\n' if exceeded else ''
    entry = f"""## {question}
글자수: {actual}자 ({'공백 포함' if count_mode == '포함' else '공백 제외'}, 목표 {char_limit}자){exceeded_note}

{answer}

---
{meta}
작성일: {today}
"""
    if out_path.exists():
        out_path.write_text(out_path.read_text(encoding='utf-8').rstrip() + '\n\n' + entry, encoding='utf-8')
    else:
        out_path.write_text(f'# {company} {position}\n\n{entry}', encoding='utf-8')

    yield 'done', {
        'answer': answer,
        'meta': meta,
        'chars': actual,
        'limit': char_limit,
        'count_mode': count_mode,
        'exceeded': exceeded,
        'retries': retries,
        # S11-4: 자동 검사 결과. ready_for_user 는 "검사를 통과했다" 이지
        # "사용자가 승인했다" 나 "제출했다" 가 아니다.
        'status': STATUS_REVIEW_REQUIRED if issues else STATUS_READY,
        'issues': issues,
        'saved_to': str(out_path.relative_to(project_root().resolve())),
        # S11-5: saved_to 는 프로젝트 루트 기준이라 화면에서 링크를 만들 때 쓰면
        # outputs/<user>/ 를 다시 떼어내야 한다(예전엔 파일명만 떼어 서브폴더를 잃었다).
        # rel_path 는 /output/<user>/<path:filename> 라우트에 그대로 붙일 수 있는 형태다.
        'rel_path': str(out_path.relative_to(out_dir)).replace('\\', '/'),
        'used_experiences': [fn for fn, _ in selected_experiences],  # 코드가 실제로 고른 파일 (LLM 자체보고가 아니다)
    }


@bp.route('/generate/<name>', methods=['POST'])
def generate(name):
    root = user_dir(name)
    data = request.get_json(silent=True) or request.form
    errors, company, position, question, char_limit, count_mode, override_files, use_trends = \
        _validate_generate_params(data)

    if errors:
        def bad():
            yield sse('error', {'message': ' '.join(errors)})
        return Response(bad(), mimetype='text/event-stream')

    def stream():
        for event, ev_data in coverletter_events(name, root, company, position, question,
                                                  char_limit, count_mode, override_files,
                                                  use_trends=use_trends):
            yield sse(event, ev_data)

    return Response(stream_with_context(stream()), mimetype='text/event-stream')


@bp.route('/generate/<name>/bundle', methods=['POST'])
def generate_bundle(name):
    """ROADMAP S7-3: 공고 하나를 고르면 이력서·자소서·포트폴리오 초안을 한 번에 생성한다.

    이력서(lib/resume.py)와 포트폴리오(lib/portfolio.py)는 매칭 결과(matches 테이블)의 근거
    경험을 재사용하므로, 이 공고를 아직 매칭한 적이 없으면 먼저 매칭부터 실행한다.
    """
    db_path = _db_path()

    root = user_dir(name)
    data = request.get_json(silent=True) or request.form
    job_id_raw = (data.get('job_id') or '').strip()
    if not job_id_raw.isdigit():
        return jsonify(success=False, error='공고를 선택해주세요.'), 400

    errors, _, _, question, char_limit, count_mode, override_files, use_trends = _validate_generate_params(
        {**data, 'company': 'x', 'position': 'x'})  # company/position 은 공고에서 가져오므로 폼 값 검증은 건너뜀
    errors = [e for e in errors if '기업명' not in e and '직무명' not in e]
    if errors:
        def bad():
            yield sse('error', {'message': ' '.join(errors)})
        return Response(bad(), mimetype='text/event-stream')

    job_id = int(job_id_raw)

    def stream():
        conn = db.get_conn(db_path)
        job_row = conn.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
        if not job_row:
            conn.close()
            yield sse('error', {'message': f'공고를 찾을 수 없습니다 (id={job_id}).'})
            return
        job = dict(job_row)
        company, position = job['company'], job['position']
        # resume/portfolio 와 같은 규칙으로 폴더명을 정리한다 — 공고 레코드의 회사명/직무명은
        # job_parser.commit() 이 파일시스템 안전성까지는 보지 않은 값이라 그대로 못 믿는다.
        folder_name = _safe_folder_name(company, position)

        exp_dir = root / 'experiences'
        match_row = conn.execute(
            'SELECT id FROM matches WHERE user = ? AND job_id = ? ORDER BY id DESC LIMIT 1', (name, job_id),
        ).fetchone()
        conn.close()
        if not match_row:
            yield sse('progress', {'doc': 'match', 'stage': 'start'})
            mr = matcher.match_job(name, job_id, exp_dir, db_path=db_path)
            if not mr.ok:
                yield sse('error', {'message': f'매칭 실패: {mr.error}'})
                return
            yield sse('progress', {'doc': 'match', 'stage': 'done', 'score': round(mr.score, 1), 'category': mr.category})

        yield sse('progress', {'doc': 'resume', 'stage': 'start'})
        profile_text = read_if_exists(root / 'profile.md')
        rr = resume.generate_resume(name, job_id, exp_dir, profile_text=profile_text,
                                     db_path=db_path, output_dir=output_dir(name))
        yield sse('progress', {'doc': 'resume', 'stage': 'done', 'ok': rr.ok, 'path': rr.path, 'error': rr.error})

        yield sse('progress', {'doc': 'portfolio', 'stage': 'start'})
        style_text = read_if_exists(root / 'writing-style.md')
        interest_text = read_if_exists(root / '관심직무.md')
        pr = portfolio.generate_portfolio(name, exp_dir, job_id=job_id, profile_text=profile_text,
                                           writing_style_text=style_text, interest_text=interest_text,
                                           db_path=db_path, output_dir=output_dir(name))
        yield sse('progress', {'doc': 'portfolio', 'stage': 'done', 'ok': pr.ok, 'path': pr.path, 'error': pr.error})

        coverletter_ok, coverletter_error, coverletter_path = False, None, None
        coverletter_status, coverletter_issues = None, []
        for event, ev_data in coverletter_events(name, root, company, position, question, char_limit,
                                                  count_mode, override_files, folder_name=folder_name,
                                                  use_trends=use_trends):
            tagged = dict(ev_data)
            tagged['doc'] = 'coverletter'
            if event == 'done':
                # S11-4: 저장 성공을 품질 통과로 해석하지 않는다. 글자수를 초과했거나
                # 기업 근거가 없는 초안은 done 으로 와도 ok 가 아니다.
                coverletter_status = ev_data.get('status', STATUS_READY)
                coverletter_issues = ev_data.get('issues', [])
                coverletter_ok = (coverletter_status == STATUS_READY)
                # S11-5: 이력서(lib/resume.py)와 포트폴리오는 output_dir 기준 상대경로를
                # 돌려준다. 자소서만 프로젝트 루트 기준(saved_to)이라 화면이 세 경로를
                # 다른 규칙으로 다뤄야 했다. rel_path 로 통일한다.
                coverletter_path = ev_data.get('rel_path')
            elif event == 'error':
                coverletter_error = ev_data.get('message')
                # error 를 그대로 흘리면 SSE 스트림이 끝나버려 이미 만든 이력서/포트폴리오
                # 결과까지 화면에 못 준다. 그래서 progress 로 감싸되 failed 임을 표시하고,
                # 최종 done 의 coverletter_ok=False 로 반드시 드러나게 한다.
                tagged['stage'] = 'failed'
            yield sse('progress', tagged)

        yield sse('done', {
            'resume_ok': rr.ok, 'resume_path': rr.path,
            'portfolio_ok': pr.ok, 'portfolio_path': pr.path,
            'coverletter_ok': coverletter_ok, 'coverletter_path': coverletter_path,
            'coverletter_error': coverletter_error,
            'coverletter_status': coverletter_status, 'coverletter_issues': coverletter_issues,
            'folder': folder_name,
        })

    return Response(stream_with_context(stream()), mimetype='text/event-stream')
