"""생성 라우트. skills/write-coverletter.md 로 문항 하나에 대한 자소서 문단을 만든다 (ROADMAP S1-3).

이 라우트에서 가장 중요한 규칙은 하나다: **글자수는 LLM 이 아니라 코드가 잰다.**
S1-1 에서 모델이 자체 보고한 글자수(683자)가 실제 글자수(715자)와 32자 어긋나는 것을 실측으로
확인했다 (PROGRESS.md 참고). 그래서 여기서는 모델이 뭐라고 보고하든 무시하고, len() 으로 직접 센다.

원본 스킬(write-coverletter.md)의 자연어 Step 9 출력 형식은 사람이 읽기엔 좋지만 코드가 안정적으로
파싱하기엔 약하다. 그래서 S1-2 와 같은 방식으로, 우리가 정한 구분자 안에 본문만 담아 달라고 요청한다
(스킬 파일 자체는 건드리지 않는다 — 1-B 원칙. 구분자는 이 라우트의 사용자 메시지에서만 요구한다).

경험 선택(Step 7)은 아직 코드로 분리되어 있지 않다 (그 분리는 S3-1 에서 한다). 지금은 사용자의
모든 경험 파일 전문을 프롬프트에 실어 LLM 이 그 안에서 고르게 한다 — 있지도 않은 경험을 지어내지
않게 하려면 실제 내용을 줘야 하기 때문이다. 경험이 많은 사용자는 호출 비용이 커진다는 게 이 방식의
약점이고, S3-1 에서 선택을 코드로 옮기면 해결된다.
"""
import json
import queue
import re
import threading
from pathlib import Path

from flask import Blueprint, Response, request, stream_with_context

from lib.llm import stream_complete
from lib.skills import load_skill
from web.paths import output_dir, project_root, safe_path, user_dir, valid_segment

bp = Blueprint('generate', __name__)

_ANSWER_BLOCK = re.compile(r'===ANSWER===\n(?P<body>.*?)\n===END===', re.S)
_META_BLOCK = re.compile(r'===META===\n(?P<body>.*?)\n===END===', re.S)

MAX_RETRIES = 1  # ROADMAP: 초과 시 재시도 1회


def count_chars(text, count_mode):
    return len(text) if count_mode == '포함' else len(re.sub(r'\s', '', text))


def read_if_exists(path: Path):
    return path.read_text(encoding='utf-8') if path.exists() else ''


def build_prompt(name, company, position, question, char_limit, count_mode,
                  profile_text, style_text, rules_text, interest_text,
                  company_text, reference_texts, experiences):
    exp_dump = '\n\n'.join(f'--- users/{name}/experiences/{fn} ---\n{text}' for fn, text in experiences)
    ref_dump = ('\n\n'.join(f'--- {fn} ---\n{text}' for fn, text in reference_texts)
                if reference_texts else '(로컬 레퍼런스 없음. MCP 는 이 환경에 연결되어 있지 않으므로 Step 4 는 생략)')
    company_note = (
        f'--- companies/{company}.md ---\n{company_text}'
        if company_text.strip()
        else f'companies/{company}.md 캐시가 없습니다. MCP 가 연결되어 있지 않으므로 Step 2 지시대로 '
             '학습 데이터 기반 일반 정보만 참고하고, 과도하게 구체적인 최신 수치·뉴스는 인용하지 마세요.'
    )
    rules_note = rules_text if rules_text.strip() else '(비어 있음 — 적용할 규칙 없음, 무시)'
    count_desc = '공백 포함' if count_mode == '포함' else '공백 제외'

    return f"""[요청]
사용자: {name}
기업명: {company}
직무명: {position}
문항: {question}
글자수: {char_limit}자 ({count_desc})

Step 1(파일 읽기)은 아래에 이미 첨부되어 있습니다. Step 5부터 Step 8까지 수행하세요.
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

[Step 7 후보 경험 전체 — 이 중에서만 골라야 합니다. 없는 경험을 지어내지 마세요]
{exp_dump}

[지시]
- Step 7 경험 선택 원칙(활동 최대 2개, 기본 1개)을 지키세요.
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


def build_retry_prompt(char_limit, count_mode, current_count):
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
참조한 기업 정보: [companies/{{company}}.md 또는 "없음 (추론)"]
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


@bp.route('/generate/<name>', methods=['POST'])
def generate(name):
    root = user_dir(name)
    data = request.get_json(silent=True) or request.form
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

    if errors:
        def bad():
            yield sse('error', {'message': ' '.join(errors)})
        return Response(bad(), mimetype='text/event-stream')

    exp_dir = root / 'experiences'
    experiences = [(f.name, f.read_text(encoding='utf-8')) for f in sorted(exp_dir.glob('*.md'))] if exp_dir.exists() else []

    def stream():
        if not experiences:
            yield sse('error', {'message': '경험 파일이 없습니다. 먼저 파싱을 진행하세요.'})
            return

        profile_text = read_if_exists(root / 'profile.md')
        style_text = read_if_exists(root / 'writing-style.md')
        rules_text = read_if_exists(root / 'writing-rules.md')
        interest_text = read_if_exists(root / '관심직무.md')
        company_text = read_if_exists(project_root() / 'companies' / f'{company}.md')

        ref_dir = project_root() / 'references' / 'coverletter' / position
        reference_texts = []
        if ref_dir.exists():
            for f in sorted(ref_dir.glob('*.md'))[:3]:
                reference_texts.append((f.name, f.read_text(encoding='utf-8')))

        yield sse('progress', {'stage': 'context_ready', 'experiences': len(experiences),
                                'company_cached': bool(company_text.strip()), 'references': len(reference_texts)})

        system = load_skill('write-coverletter')
        user_msg = build_prompt(name, company, position, question, char_limit, count_mode,
                                 profile_text, style_text, rules_text, interest_text,
                                 company_text, reference_texts, experiences)
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
                        yield sse('progress', {'stage': 'generating'})
                        chars_since_last = 0
                    continue
                if kind == 'error':
                    raise RuntimeError(payload)
                return payload

        try:
            result = yield from call_llm(messages)
        except RuntimeError as e:
            yield sse('error', {'message': f'LLM 호출 실패: {e}'})
            return

        answer, meta = parse_answer(result['text'])
        actual = count_chars(answer, count_mode)
        yield sse('progress', {'stage': 'draft', 'chars': actual, 'limit': char_limit})

        # exceeded 는 "초과했다" 라는 뜻 그대로 유지한다 (파일 경고, 완료 이벤트, ROADMAP 완료 조건이
        # 전부 이 이름으로 추적한다). 재시도를 시킬지 여부(needs_retry)는 이보다 넓다 — 스킬 Step 9 가
        # "여유 있게 짧게 쓰는 것 금지" 라고 못박았으므로, 많이 모자란 경우도 다시 시킨다.
        retries = 0
        exceeded = actual > char_limit
        needs_retry = exceeded or abs(actual - char_limit) / char_limit > 0.05
        while needs_retry and retries < MAX_RETRIES:
            retries += 1
            yield sse('progress', {'stage': 'retry', 'attempt': retries, 'diff': actual - char_limit})
            messages = messages + [
                {'role': 'assistant', 'content': result['text']},
                {'role': 'user', 'content': build_retry_prompt(char_limit, count_mode, actual)},
            ]
            try:
                result = yield from call_llm(messages)
            except RuntimeError as e:
                yield sse('error', {'message': f'재시도 중 LLM 호출 실패: {e}'})
                return
            answer, meta = parse_answer(result['text'])
            actual = count_chars(answer, count_mode)
            exceeded = actual > char_limit
            needs_retry = exceeded or abs(actual - char_limit) / char_limit > 0.05

        if not answer:
            yield sse('error', {'message': 'LLM 출력에서 ANSWER 블록을 찾지 못했습니다.', 'raw': result['text'][:500]})
            return

        out_dir = output_dir(name)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = safe_path(out_dir, f'{company}_{position}.md')
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

        yield sse('done', {
            'answer': answer,
            'meta': meta,
            'chars': actual,
            'limit': char_limit,
            'count_mode': count_mode,
            'exceeded': exceeded,
            'retries': retries,
            'saved_to': str(out_path.relative_to(project_root().resolve())),
        })

    return Response(stream_with_context(stream()), mimetype='text/event-stream')
