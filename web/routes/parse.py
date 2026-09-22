"""파싱 라우트. raw/ 파일을 읽어 skills/parse-experiences.md 로 파싱하고 experiences/ 에 저장한다 (ROADMAP S1-2).

원본 스킬(parse-experiences.md)은 대화형 확인이 세 군데 있다. 웹은 1회성 요청이라 그 자리에서
되물을 수 없으므로, 아래처럼 자동화한다. 나중에 바꾸고 싶으면 이 세 가지부터 본다.

1. 재파싱 여부 확인 → experiences/ 가 이미 있으면 거부하고 force=1 을 요구한다.
   "신규 파일만 추가 파싱"은 이번 버전에 없다 (raw 파일과 experiences 파일을 잇는 기록이 아직 없어서
   무엇이 신규인지 판단할 수 없다). 전체 재파싱만 지원한다.
2. 오래된 경험(5년 이상 / 대학 입학 이전) 포함 여부 확인 → 항상 포함하고 "⚠️ 오래된 경험" 태그만 붙인다.
   write-coverletter.md Step 7 이 이 태그를 보고 생성 시점에 사용자에게 되묻는다 (web/data.py 도 이미 이 태그를 읽어 화면에 표시한다).
   확인 지점을 파싱 시점에서 생성 시점으로 옮긴 것뿐이다.
3. 관심직무.md 추론 확인 → 비어 있으면 자동으로 초안을 만든다.

raw/ 루트에 분류 안 된 파일이 있으면(0-A 자동 분류 대상) 건너뛰고 skipped 에 표시한다.
웹 업로드(/upload)는 항상 raw/apply 또는 raw/logs 에 바로 저장하므로 이 경우는 드물다.
"""
import json
import queue
import re
import threading
from pathlib import Path

from flask import Blueprint, Response, request, stream_with_context

from lib.extract import extract as extract_file
from lib.llm import stream_complete
from lib.skills import load_skill
from web.paths import project_root, user_dir

bp = Blueprint('parse', __name__)


def extract_text(path: Path):
    """(ok, text, note) 를 돌려준다. lib/extract.py 의 얇은 래퍼다 (S2-1 이전 자체 구현은 제거됨).
    이 함수가 반환 형태를 유지하는 건 이 라우트 안의 나머지 코드와 기존 테스트가 튜플을 기대해서다."""
    r = extract_file(path)
    return r.ok, r.text, r.error or ''


# ── LLM 프롬프트 구성과 결과 파싱 ────────────────────────────────────────────────────
# LLM 출력은 "===FILE: 상대경로===\n내용\n===END===" 블록을 이어붙인 형태로 받는다.
# 상대경로는 반드시 요청한 사용자 폴더 밑이어야 하며, 그 밖을 가리키면 그 파일만 버리고 계속한다.

_FILE_BLOCK = re.compile(r'===FILE:\s*(?P<path>[^\n=]+?)\s*===\n(?P<body>.*?)\n===END===', re.S)
_REPORT_BLOCK = re.compile(r'===REPORT===\n(?P<body>.*?)\n===END===', re.S)


EXPERIENCE_TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / 'templates' / 'experience-entry.md'
INTEREST_TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / 'templates' / 'user-skeleton' / '관심직무.md'


def build_prompt(name, extracted, skipped, profile_text, interest_text, writing_rules_exists):
    sections = []
    for kind, filename, text in extracted:
        sections.append(f'--- raw/{kind}/{filename} ---\n{text}')
    raw_dump = '\n\n'.join(sections)

    skipped_note = ''
    if skipped:
        lines = '\n'.join(f'- {fn}: {note}' for fn, note in skipped)
        skipped_note = f'\n\n[읽지 못해 건너뛴 파일 — 완료 보고의 "변환 실패 또는 내용 없음" 에 그대로 반영하세요]\n{lines}'

    if interest_text.strip():
        interest_note = f'--- users/{name}/관심직무.md (이미 내용이 있음 — 그대로 둔다) ---\n{interest_text}'
    else:
        interest_template = INTEREST_TEMPLATE_PATH.read_text(encoding='utf-8')
        interest_note = (
            'Step 5 절차대로 raw 와 생성한 경험에서 추론해 초안을 만드세요. '
            '아래 templates/user-skeleton/관심직무.md 의 섹션 구성(### 핵심 키워드 / ### 추천 표현 / '
            '### 피해야 할 표현 / ### 이 직무에서 강조할 경험 유형)을 그대로 따르되, 대괄호 예시는 실제 내용으로 채우세요.\n\n'
            f'--- templates/user-skeleton/관심직무.md ---\n{interest_template}'
        )

    rules_note = (
        '이미 존재하므로 만들지 않습니다.'
        if writing_rules_exists
        else 'Step 2 의 템플릿(## 구조 / ## 기타 뼈대)으로 새로 만드세요.'
    )

    experience_template = EXPERIENCE_TEMPLATE_PATH.read_text(encoding='utf-8')

    return f"""[요청]
사용자: {name}
아래는 raw/apply 와 raw/logs 에서 추출한 원문입니다. Step 0-A(재파싱 분기)는 이미 처리되었으니 건너뛰고
Step 1부터 Step 6까지 수행하세요.

이 호출은 대화형 확인 없이 한 번에 끝나야 하므로, 원래 스킬의 확인 지점 세 곳을 아래처럼 자동 처리합니다.
- 오래된 경험(5년 이상 전 / 대학 입학 이전)은 제외하지 말고 전부 포함하되, 파일 하단에 "⚠️ 오래된 경험"
  한 줄을 추가하세요 (사용자 확인은 자소서 작성 시점으로 미룹니다).
- 중복 판단이 애매하면 스킬 지시대로 별도 파일로 만들고 "⚠️ 확인 필요" 표시만 남기세요 (되묻지 않습니다).
- 관심직무.md: {interest_note}

writing-rules.md: {rules_note}

--- users/{name}/profile.md (현재 내용) ---
{profile_text}

{raw_dump}{skipped_note}

[출력 형식 — 반드시 이 형식만 사용]
만들거나 갱신할 파일마다 아래 블록을 반복하세요. path 는 "users/{name}/..." 로 시작하는 상대경로입니다.

===FILE: users/{name}/writing-style.md===
(전체 내용)
===END===

writing-rules.md 를 새로 만드는 경우에만:
===FILE: users/{name}/writing-rules.md===
(전체 내용)
===END===

profile.md 는 가치관 관련 섹션만 보완한 전체 파일을 다시 출력하세요 (다른 섹션은 그대로 보존):
===FILE: users/{name}/profile.md===
(전체 내용)
===END===

경험마다, 아래 templates/experience-entry.md 의 섹션 이름과 순서를 정확히 그대로 사용하세요
(Situation/Action/Result 세 항목만 씁니다. Task 항목을 추가하지 마세요.
"## STAR 품질 체크" 체크리스트도 실제 체크([x]/[ ])와 함께 반드시 포함하세요.
"## 활동 내용" 에는 2~3줄 요약을 쓰세요. 파일명 규칙: [연도]-[활동명요약].md):

--- templates/experience-entry.md ---
{experience_template}
---

===FILE: users/{name}/experiences/[파일명].md===
(전체 내용)
===END===

===FILE: users/{name}/index.md===
(전체 내용)
===END===

관심직무.md 를 새로 만드는 경우에만:
===FILE: users/{name}/관심직무.md===
(전체 내용)
===END===

마지막으로 Step 7 형식의 완료 보고를 아래 블록에 넣으세요:
===REPORT===
(완료 보고 본문)
===END===
"""


def parse_result(text, project_root: Path, root: Path):
    """LLM 출력에서 ===FILE=== 블록을 찾아 root(이 사용자 폴더) 밑에만 쓴다.

    project_root 는 PROJECT_ROOT, root 는 PROJECT_ROOT/users/[이름] (둘 다 이미 resolve() 된 상태).
    각 블록의 path 는 "users/[이름]/..." 형태의, PROJECT_ROOT 기준 상대경로다.
    반환: (written: [str], rejected: [(path, reason)], report: str)
    """
    written, rejected = [], []
    for m in _FILE_BLOCK.finditer(text):
        rel = m.group('path').strip()
        body = m.group('body')
        candidate = (project_root / rel).resolve()
        if not candidate.is_relative_to(root):
            rejected.append((rel, '이 사용자 폴더 밖 경로라 거부'))
            continue
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(body.strip() + '\n', encoding='utf-8')
        written.append(str(candidate.relative_to(project_root)))

    report_m = _REPORT_BLOCK.search(text)
    report = report_m.group('body').strip() if report_m else ''
    return written, rejected, report


# ── SSE 헬퍼 ────────────────────────────────────────────────────────────────────

def sse(event, data):
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


@bp.route('/parse/<name>', methods=['POST'])
def parse_user(name):
    root = user_dir(name)
    exp_dir = root / 'experiences'
    force = request.values.get('force') == '1'

    existing = sorted(p.name for p in exp_dir.glob('*.md')) if exp_dir.exists() else []

    def stream():
        if existing and not force:
            yield sse('error', {
                'message': f'이미 파싱된 경험이 {len(existing)}개 있습니다. 전체를 재파싱하려면 force=1 로 다시 요청하세요.',
                'existing_count': len(existing),
            })
            return

        apply_dir, logs_dir = root / 'raw' / 'apply', root / 'raw' / 'logs'
        apply_files = sorted(p for p in apply_dir.glob('*') if p.is_file()) if apply_dir.exists() else []
        logs_files = sorted(p for p in logs_dir.glob('*') if p.is_file()) if logs_dir.exists() else []

        if not apply_files and not logs_files:
            yield sse('error', {'message': 'raw/ 폴더에 자소서 파일이 없습니다.'})
            return

        extracted, skipped = [], []
        for kind, files in (('apply', apply_files), ('logs', logs_files)):
            for f in files:
                ok, text, note = extract_text(f)
                yield sse('progress', {'stage': 'extract', 'file': f.name, 'kind': kind, 'ok': ok, 'note': note})
                (extracted if ok else skipped).append((kind, f.name, text) if ok else (f.name, note))

        if not extracted:
            yield sse('error', {'message': '읽을 수 있는 파일이 없습니다.', 'skipped': skipped})
            return

        yield sse('progress', {'stage': 'llm_start', 'input_files': len(extracted), 'skipped': len(skipped)})

        profile_text = (root / 'profile.md').read_text(encoding='utf-8') if (root / 'profile.md').exists() else ''
        interest_path = root / '관심직무.md'
        interest_text = interest_path.read_text(encoding='utf-8') if interest_path.exists() else ''
        rules_exists = (root / 'writing-rules.md').exists()

        system = load_skill('parse-experiences')
        user_msg = build_prompt(name, extracted, skipped, profile_text, interest_text, rules_exists)

        q = queue.Queue()

        def worker():
            try:
                result = stream_complete(system=system, messages=[{'role': 'user', 'content': user_msg}],
                                          max_tokens=16000, on_delta=lambda c: q.put(('delta', c)))
                q.put(('done', result))
            except Exception as e:  # noqa: BLE001 — SSE 로 에러를 전달해야 한다
                q.put(('error', str(e)))

        threading.Thread(target=worker, daemon=True).start()

        # LLM 호출은 수십 초가 걸린다. delta 를 매번 그대로 내보내면 이벤트가 너무 잦으니
        # 일정량(약 400자)마다 한 번씩만 "생성 중" 진행 신호를 보낸다.
        result = None
        chars_since_last = 0
        while True:
            kind, payload = q.get()
            if kind == 'delta':
                chars_since_last += len(payload)
                if chars_since_last >= 400:
                    yield sse('progress', {'stage': 'generating'})
                    chars_since_last = 0
                continue
            if kind == 'error':
                yield sse('error', {'message': f'LLM 호출 실패: {payload}'})
                return
            result = payload
            break

        yield sse('progress', {'stage': 'llm_done', 'usage': result['usage']})

        written, rejected, report = parse_result(result['text'], project_root().resolve(), root.resolve())
        for w in written:
            yield sse('progress', {'stage': 'write', 'file': w})
        for rel, reason in rejected:
            yield sse('progress', {'stage': 'reject', 'file': rel, 'reason': reason})

        yield sse('done', {
            'written': written,
            'rejected': rejected,
            'skipped': [s[0] if isinstance(s, tuple) else s for s in skipped],
            'report': report,
        })

    return Response(stream_with_context(stream()), mimetype='text/event-stream')
