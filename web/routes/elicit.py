"""대화형 경험 발굴 라우트 (ROADMAP S3-4). skills/elicit-experiences.md 로 한 턴씩 대화한다.

이 스킬은 한 번에 질문 하나씩, 여러 턴에 걸쳐 진행된다. HTTP 는 상태가 없으므로 대화 기록은
클라이언트(브라우저)가 들고 있다가 매 턴 그대로 다시 보낸다 — 서버는 세션을 저장하지 않는다.
(로그인/세션이 아직 없다 — S10. 그때 가서 서버 쪽 대화 저장으로 옮길 수도 있다.)

스킬이 사용자의 승인을 받은 뒤 ===EXPERIENCE_READY=== 블록을 출력하면, 그 즉시 코드가
experiences/ 에 저장한다 (스킬 Step 9 가 그렇게 하기로 되어 있다). 승인 여부 판단은 대화
안에서 모델이 이미 끝냈으므로 별도의 "저장" 버튼은 없다.

elicit-experiences.md 는 Step 8 에서 "templates/experience-entry.md 형식으로" 라고만 말로
지시한다. S1-2 에서 이렇게 말로만 시키면 모델이 자기 나름의 구조를 지어낸다는 것을 실측으로
확인했다 (Situation/Action/Result 대신 Task 항목을 넣거나, STAR 품질 체크 섹션을 통째로 빠뜨림).
그래서 매 턴 system 프롬프트에 템플릿 원문을 직접 붙여서 보낸다 — 스킬 파일 자체는 건드리지
않는다 (1-B 원칙. templates/experience-entry.md 도 write-coverletter.md/parse-experiences.md 가
이미 참조하는 공용 템플릿이라 이걸 스킬 파일 안에 복사해 넣으면 그것대로 또 다른 중복이 된다).
"""
import re
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from lib.llm import stream_complete
from lib.skills import load_skill
from web.paths import safe_path, user_dir, valid_segment

bp = Blueprint('elicit', __name__)

_READY_BLOCK = re.compile(
    r'===EXPERIENCE_READY===\s*\n파일명:\s*(?P<filename>[^\n]+)\n+(?P<body>.*?)\n===END===', re.S)
_TITLE = re.compile(r'^# (.+)$', re.M)
_TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / 'templates' / 'experience-entry.md'

MAX_HISTORY_MESSAGES = 60  # 안전장치. 한 세션 경험 1~2개면 보통 20턴 안팎이면 끝난다.


def build_system_prompt():
    template = _TEMPLATE_PATH.read_text(encoding='utf-8')
    return (
        load_skill('elicit-experiences')
        + '\n\n---\n\n## 참고: templates/experience-entry.md 원문 (Step 8 에서 반드시 이 구조를 그대로 따를 것)\n\n'
        + template
    )


@bp.route('/elicit/<name>', methods=['GET'])
def elicit_page(name):
    if not user_dir(name).exists():
        return redirect(url_for('main.index'))
    return render_template('elicit.html', user=name)


@bp.route('/elicit/<name>', methods=['POST'])
def elicit_turn(name):
    root = user_dir(name)
    data = request.get_json(silent=True) or {}
    raw_history = data.get('messages') or []
    user_message = (data.get('message') or '').strip()

    if not isinstance(raw_history, list) or len(raw_history) > MAX_HISTORY_MESSAGES:
        return jsonify(success=False, error='대화가 너무 깁니다. 새로 시작해주세요.'), 400

    messages = []
    for m in raw_history:
        if isinstance(m, dict) and m.get('role') in ('user', 'assistant') and isinstance(m.get('content'), str):
            messages.append({'role': m['role'], 'content': m['content']})

    if user_message:
        messages.append({'role': 'user', 'content': user_message})
    elif not messages:
        # 페이지를 열자마자 첫 인사(Step 0)를 받기 위한 시작 신호. 화면에는 이 턴을 보여주지 않는다.
        messages.append({'role': 'user', 'content': '(대화를 시작해주세요)'})
    else:
        return jsonify(success=False, error='메시지가 없습니다.'), 400

    system = build_system_prompt()
    try:
        result = stream_complete(system=system, messages=messages, max_tokens=1500)
    except Exception as e:  # noqa: BLE001 — 대화형이라 라우트가 죽으면 안 된다
        return jsonify(success=False, error=f'LLM 호출 실패: {e}')

    reply = result['text']
    saved = None
    display_reply = reply

    m = _READY_BLOCK.search(reply)
    if m:
        filename = m.group('filename').strip()
        body = m.group('body').strip()
        if valid_segment(filename) and filename.lower().endswith('.md'):
            exp_dir = root / 'experiences'
            exp_dir.mkdir(parents=True, exist_ok=True)
            path = safe_path(exp_dir, filename)
            path.write_text(body + '\n', encoding='utf-8')
            title_m = _TITLE.search(body)
            saved = {'filename': filename, 'title': title_m.group(1).strip() if title_m else filename}
        # 화면에는 기계용 블록을 보여주지 않고, 블록 앞의 대화문 + 짧은 완료 알림만 남긴다
        display_reply = reply[:m.start()].strip()
        if saved:
            display_reply = (display_reply + f'\n\n✅ "{saved["title"]}" 저장 완료.').strip()
        else:
            display_reply = (display_reply + '\n\n(저장에 실패했습니다 — 파일명을 확인해주세요.)').strip()

    # 대화 기록에는 원문(블록 포함)을 남겨야 다음 턴에 모델이 자기가 뭘 했는지 기억한다
    messages.append({'role': 'assistant', 'content': reply})

    return jsonify(success=True, reply=display_reply, messages=messages, saved=saved)
