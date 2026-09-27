"""대화형 경험 발굴 라우트 (ROADMAP S3-4). skills/elicit-experiences.md 로 한 턴씩 대화한다.

이 스킬은 한 번에 질문 하나씩, 여러 턴에 걸쳐 진행된다. HTTP 는 상태가 없으므로 대화 기록은
클라이언트(브라우저)가 들고 있다가 매 턴 그대로 다시 보낸다 — 서버는 세션을 저장하지 않는다.
(로그인/세션이 아직 없다 — S10. 그때 가서 서버 쪽 대화 저장으로 옮길 수도 있다.)

(새 경험 만들기 모드) 스킬이 사용자의 승인을 받은 뒤 ===EXPERIENCE_READY=== 블록을 출력하면,
그 즉시 코드가 experiences/ 에 저장한다 (스킬 Step 9 가 그렇게 하기로 되어 있다). 승인 여부
판단은 대화 안에서 모델이 이미 끝냈으므로 별도의 "저장" 버튼은 없다.

(경험 보강 모드, 2026-09-27 고도화) `?file=<filename>` 로 들어오면 다르게 동작한다 — 이
블록이 나와도 자동 저장하지 않고, 상세 페이지(editor.html)에 임베드된 미리보기만 갱신한다.
실제 저장은 사용자가 상세 페이지의 "저장" 버튼을 직접 눌러야 일어난다(사용자 요청: "실시간
으로 반영돼서 저장을 누르면 저장이 되면 좋겠다"). 아래 build_enrichment_system_prompt()/
elicit_turn() 의 draft 분기 참고.

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


# 고도화(2026-09-27, 사용자 요청): "⚠️ 표시된 것들 대화로 내용 추가할 수 있으면 좋겠다".
# elicit-experiences.md 는 "raw/ 가 아예 없는 사용자가 처음부터 새 경험을 만드는" 시나리오
# 전용이라(Step 1~3이 "어느 시기의 활동을 나열해서 하나 고르기"), 이미 있는 경험 하나를
# 보강하는 데는 안 맞는다 — 그래서 그 스킬 파일은 그대로 두고(1-B 원칙, elicit.py 자체 docstring
# 의 설명과 같은 이유), 보강 모드 전용 프롬프트를 새로 만든다.
_ENRICH_INSTRUCTIONS = """당신은 이미 저장되어 있는 경험 파일 하나를 사용자와의 대화로
"보강"하는 중입니다. 새 경험을 처음부터 만드는 게 아닙니다.

- 아래 "현재 파일 내용"에서 이미 채워진 항목은 다시 묻지 않습니다.
- "⚠️ STAR 보강 필요", "⚠️ 오래된 경험" 표시가 있거나 비어 있는 항목을 하나씩 순서대로
  채우도록 질문하세요. 한 번에 질문 하나만 합니다. 답변을 요약해 "이렇게 이해했습니다:
  [요약]"으로 확인받은 뒤에만 다음 질문으로 넘어갑니다.
- 사용자가 말하지 않은 수치나 사실을 절대 지어내지 않습니다. 그래도 안 채워지는 항목은
  ⚠️ STAR 보강 필요로 남겨둡니다.
- **중요**: 답변 하나를 반영했다고 대화가 끝난 게 아닙니다. 아직 ⚠️ 표시가 남아있거나 비어
  있는 다른 항목이 있으면, 그 답변을 요약해 확인한 바로 다음에 이어서 다음 항목에 대한
  질문을 반드시 계속합니다. 모든 항목을 다 채웠거나 사용자가 "여기까지"/"그만할게요"라고
  명시적으로 말했을 때만 대화를 마무리합니다.
- 답변 하나를 반영할 때마다, 매 응답 끝에 파일 전체를 다시 정리해서 아래 형식의 블록을
  덧붙이세요(질문을 다 마친 뒤 한 번만 내는 게 아니라 매번). 이 블록은 화면에 보이지
  않고 위쪽 미리보기만 조용히 갱신합니다 — 대화는 이 블록과 무관하게 계속됩니다. 순서는
  항상: (1) 답변 요약, (2) 아직 남은 항목이 있으면 다음 질문 (없으면 마무리 인사), (3) 그
  다음 마지막 줄에 이 블록. 파일명은 절대 바꾸지 않습니다(항상 원래 파일명 그대로 씁니다):

===EXPERIENCE_READY===
파일명: {filename}
(templates/experience-entry.md 형식 전체 — 기존 내용 + 보강된 내용, 완전한 문서)
===END===
"""


def build_enrichment_system_prompt(filename, existing_content):
    template = _TEMPLATE_PATH.read_text(encoding='utf-8')
    return (
        _ENRICH_INSTRUCTIONS.format(filename=filename)
        + '\n\n---\n\n## 참고: templates/experience-entry.md 형식\n\n' + template
        + '\n\n---\n\n## 현재 파일 내용\n\n' + existing_content
    )


@bp.route('/elicit/<name>', methods=['GET'])
def elicit_page(name):
    if not user_dir(name).exists():
        return redirect(url_for('main.index'))

    edit_filename = (request.args.get('file') or '').strip()
    edit_title = None
    edit_content = None
    if edit_filename:
        if not valid_segment(edit_filename) or not edit_filename.lower().endswith('.md'):
            return redirect(url_for('main.user_detail', name=name))
        exp_path = safe_path(user_dir(name) / 'experiences', edit_filename)
        if not exp_path.exists():
            return redirect(url_for('main.user_detail', name=name))
        edit_content = exp_path.read_text(encoding='utf-8')
        m = _TITLE.search(edit_content)
        edit_title = m.group(1).strip() if m else edit_filename
    else:
        edit_filename = None

    return render_template('elicit.html', user=name, edit_filename=edit_filename,
                           edit_title=edit_title, edit_content=edit_content)


@bp.route('/elicit/<name>', methods=['POST'])
def elicit_turn(name):
    root = user_dir(name)
    data = request.get_json(silent=True) or {}
    raw_history = data.get('messages') or []
    user_message = (data.get('message') or '').strip()
    edit_filename = (data.get('edit_filename') or '').strip() or None

    if not isinstance(raw_history, list) or len(raw_history) > MAX_HISTORY_MESSAGES:
        return jsonify(success=False, error='대화가 너무 깁니다. 새로 시작해주세요.'), 400

    existing_content = None
    if edit_filename:
        if not valid_segment(edit_filename) or not edit_filename.lower().endswith('.md'):
            return jsonify(success=False, error='잘못된 파일명입니다.'), 400
        exp_path = safe_path(root / 'experiences', edit_filename)
        if not exp_path.exists():
            return jsonify(success=False, error='보강할 경험 파일을 찾을 수 없습니다.'), 404
        existing_content = exp_path.read_text(encoding='utf-8')

    is_first_turn = not raw_history  # 고도화(2026-09-27): "저장 버튼 안내는 시작할 때 한 번만"

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

    system = (build_enrichment_system_prompt(edit_filename, existing_content)
              if edit_filename else build_system_prompt())
    try:
        # 실측(2026-09-27)으로 발견한 버그: 보강 모드는 매 턴 문서 전체를 다시 출력해야
        # 하는데(그래야 미리보기가 실시간으로 갱신된다), 1500 으로는 STAR 섹션이 다 채워질
        # 수록 자꾸 잘렸다. 잘리면 ===END=== 가 안 나와서 _READY_BLOCK 이 매치를 못 하고,
        # 그러면 기계용 블록이 통째로 화면(채팅)에 그대로 노출되고 미리보기도 갱신되지
        # 않는 이중 버그로 이어졌다(사용자가 실제 대화 로그로 지적). lib/trends.py 의
        # max_tokens 잘림 버그와 같은 종류 — 넉넉히 올린다.
        result = stream_complete(system=system, messages=messages, max_tokens=8192)
    except Exception as e:  # noqa: BLE001 — 대화형이라 라우트가 죽으면 안 된다
        return jsonify(success=False, error=f'LLM 호출 실패: {e}')

    reply = result['text']
    saved = None
    draft = None
    display_reply = reply

    if result['stop_reason'] == 'max_tokens' and not _READY_BLOCK.search(reply):
        # 그래도 잘렸다면(예: 사용자가 아주 긴 내용을 한 번에 말한 경우), 반쯤 나온 기계용
        # 블록을 그대로 채팅에 보여주지 않는다 — trends.py 와 같은 원칙: 검증 가능한 잘림을
        # 감추지 않되, 사용자에게는 깨진 원문 대신 명확한 안내를 준다.
        messages.append({'role': 'assistant', 'content': reply})
        return jsonify(success=True, messages=messages, saved=None, draft=None,
                       reply='(응답이 길어져 중간에 잘렸습니다. "계속해줘"라고 보내거나, '
                             '조금 더 짧게 나눠서 답해주시겠어요?)')

    m = _READY_BLOCK.search(reply)
    if m:
        body = m.group('body').strip()
        title_m = _TITLE.search(body)
        title = title_m.group(1).strip() if title_m else None

        if edit_filename:
            # 고도화(2026-09-27, 사용자 요청): 보강 모드는 더 이상 자동 저장하지 않는다 —
            # 상세 페이지 미리보기에 이 초안을 반영만 해두고, 사용자가 상세 페이지의 "저장"
            # 버튼을 직접 눌러야 파일에 반영된다(기존 /experience/.../save 라우트 재사용).
            # 파일명도 모델이 뭐라고 쓰든 무시하고 항상 기존 파일명 그대로 쓴다(코드가
            # 검증할 수 있는 걸 LLM 자체 보고로 대신하지 않는다는 이 프로젝트 원칙 재사용
            # — job_parser 의 공고 ID/수집일 강제 덮어쓰기와 같은 이유).
            draft = {'filename': edit_filename, 'title': title or edit_filename, 'content': body}
            # 대화는 이 블록과 무관하게 계속돼야 한다(사용자 지적: "한 곳 고치면 대화가
            # 끝나는 게 아니라 계속 이어졌으면 좋겠다") — 블록 앞뒤 텍스트를 모두 남기고
            # 블록 자체만 화면에서 지운다. 프롬프트상 블록은 맨 끝에 오길 기대하지만, 모델이
            # 다른 순서로 내는 경우에도 텍스트가 사라지지 않도록 양쪽 다 챙긴다.
            display_reply = (reply[:m.start()] + reply[m.end():]).strip()
        else:
            filename = m.group('filename').strip()
            if valid_segment(filename) and filename.lower().endswith('.md'):
                exp_dir = root / 'experiences'
                exp_dir.mkdir(parents=True, exist_ok=True)
                path = safe_path(exp_dir, filename)
                path.write_text(body + '\n', encoding='utf-8')
                saved = {'filename': filename, 'title': title or filename}
            # 화면에는 기계용 블록을 보여주지 않고, 블록 앞의 대화문 + 짧은 완료 알림만 남긴다
            display_reply = reply[:m.start()].strip()
            if saved:
                display_reply = (display_reply + f'\n\n✅ "{saved["title"]}" 저장 완료.').strip()
            else:
                display_reply = (display_reply + '\n\n(저장에 실패했습니다 — 파일명을 확인해주세요.)').strip()

    # 저장 버튼 안내는 대화 시작할 때 딱 한 번만 (사용자 지적: "그 뒤로는 안 했으면 해") —
    # 매 draft 마다 반복하지 않고, 첫 턴(=페이지를 막 열었을 때의 인사)에만 붙인다.
    if edit_filename and is_first_turn:
        display_reply = (display_reply + '\n\n💡 참고로 대화 중간중간 위 본문 미리보기가 '
                         '갱신됩니다. 마음에 드는 시점에 언제든 "저장" 버튼을 눌러 반영해주세요.').strip()

    # 대화 기록에는 원문(블록 포함)을 남겨야 다음 턴에 모델이 자기가 뭘 했는지 기억한다
    messages.append({'role': 'assistant', 'content': reply})

    return jsonify(success=True, reply=display_reply, messages=messages, saved=saved, draft=draft)
