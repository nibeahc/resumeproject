"""공고 파서 (ROADMAP S4-2). skills/parse-job.md 를 system 프롬프트로 써서
사용자가 붙여넣은 채용공고 본문을 구조화하고 jobs/*.md + jobs 테이블 행으로 저장한다.

ROADMAP 1-D(채용 사이트 스크래핑 금지): url 인자는 원문 링크를 기록하는 용도로만 쓴다.
이 모듈은 어떤 URL 도 직접 요청(fetch)하지 않는다 — 실제로 파싱하는 것은 raw_text 로
넘어온, 사용자가 직접 붙여넣은 텍스트뿐이다.

web/routes/parse.py 와 같은 패턴이다: skill 파일을 system 프롬프트로 읽고, LLM 출력을
구분자 블록으로 받아 코드가 파싱한다. 다른 점은 결과를 experiences/*.md 가 아니라
jobs/*.md + SQLite jobs 테이블에 반반씩 쓴다는 것 — 사람은 JOB_MARKDOWN 만 읽고,
매칭(S5)이나 DB 조회는 REQUIRED_JSON/PREFERRED_JSON/META_JSON 만 쓴다. 필수조건 배열이
마크다운 체크박스 정규식 파싱이 아니라 이 JSON 블록에서 그대로 오는 이유는, 자소서
글자수(S1-1)에서 모델의 자체 보고를 못 믿었던 것과 같은 이유다 — 마크다운 체크박스를
다시 정규식으로 긁는 것보다, 모델이 애초에 기계가 읽을 형태로 낸 것을 그대로 쓰는 편이
안전하다.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

from lib import db
from lib.llm import stream_complete
from lib.skills import load_skill

JOB_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / 'templates' / 'job-entry.md'

_MARKDOWN_BLOCK = re.compile(r'===JOB_MARKDOWN===\s*\n(?P<body>.*?)\n===END===', re.S)
_REQUIRED_BLOCK = re.compile(r'===REQUIRED_JSON===\s*\n(?P<body>.*?)\n===END===', re.S)
_PREFERRED_BLOCK = re.compile(r'===PREFERRED_JSON===\s*\n(?P<body>.*?)\n===END===', re.S)
_META_BLOCK = re.compile(r'===META_JSON===\s*\n(?P<body>.*?)\n===END===', re.S)

_DEADLINE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
MIN_RAW_LENGTH = 50  # 이보다 짧으면 공고 본문이 아니라 링크나 한 줄 메모로 보고 거부한다


@dataclass
class JobResult:
    ok: bool
    duplicate: bool = False
    saved: bool = False                  # jobs 테이블 + 파일까지 실제로 썼는가 (preview() 는 False)
    job_id: Optional[int] = None
    source: str = 'manual'
    source_job_id: str = ''
    path: str = ''                       # jobs/ 기준 상대 경로
    company: str = ''
    position: str = ''
    deadline: Optional[str] = None
    employment_type: Optional[str] = None
    required: List[str] = field(default_factory=list)
    preferred: List[str] = field(default_factory=list)
    markdown: str = ''                   # preview() 가 commit() 에 그대로 넘겨주는 값
    raw_text: str = ''
    content_hash: str = ''
    url: str = ''
    error: str = ''


def content_hash(raw_text: str) -> str:
    """공백을 정규화한 뒤 해시한다 — 같은 공고를 붙여넣기 방식(줄바꿈, 앞뒤 공백)만 다르게
    붙여넣어도 같은 해시가 나오게 한다."""
    normalized = re.sub(r'\s+', ' ', raw_text).strip()
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def find_by_hash(hash_value: str, db_path=None):
    conn = db.get_conn(db_path)
    try:
        row = conn.execute('SELECT * FROM jobs WHERE content_hash = ?', (hash_value,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _normalize_markdown_meta(markdown: str, source: str, source_job_id: str) -> str:
    """LLM 이 채운 메타 중 코드만 정확히 아는 값(공고 ID, 수집일)을 실제 값으로 강제한다.

    실측(S4-2)에서 두 가지가 확인됐다: (1) 공고 ID는 실제로 코드가 이 함수를 호출하기 *전에는*
    정해지지 않는데, LLM 은 templates/job-entry.md 의 예시값("manual-0001")을 그대로 베끼거나
    임의의 번호를 써서 실제 파일명(예: manual-0002.md)과 어긋났다. (2) 수집일은 애초에 오늘
    날짜를 모델이 알 방법이 없는 값인데도 프롬프트가 채우라고 하니 실행마다 다른 날짜를
    지어냈다(같은 세션에서도 2026.02.14, 2025.01.01 처럼 제각각). 둘 다 "모델이 알 수 없는
    사실은 코드가 정한다"는 원칙으로 여기서 덮어쓴다."""
    today = date.today().strftime('%Y.%m.%d')
    markdown = re.sub(r'(?m)^- 출처:.*$', f'- 출처: {source}', markdown, count=1)
    markdown = re.sub(r'(?m)^- 공고 ID:.*$', f'- 공고 ID: {source}-{source_job_id}', markdown, count=1)
    markdown = re.sub(r'(?m)^- 수집일:.*$', f'- 수집일: {today}', markdown, count=1)
    return markdown


def _next_manual_id(conn) -> str:
    rows = conn.execute("SELECT source_job_id FROM jobs WHERE source = 'manual'").fetchall()
    nums = [int(m.group(1)) for r in rows if r['source_job_id'] for m in [re.fullmatch(r'(\d+)', r['source_job_id'])] if m]
    return f'{(max(nums) + 1) if nums else 1:04d}'


def build_prompt(raw_text: str, url: str = '', company_hint: str = '', position_hint: str = '') -> str:
    template = JOB_TEMPLATE_PATH.read_text(encoding='utf-8')
    hints = []
    if url.strip():
        hints.append(f'- 원문 URL (참고용 — 이 URL 을 직접 열어보지 마세요): {url.strip()}')
    if company_hint.strip():
        hints.append(f'- 회사명 힌트(원문에서 못 찾으면 이 값을 씁니다): {company_hint.strip()}')
    if position_hint.strip():
        hints.append(f'- 직무명 힌트(원문에서 못 찾으면 이 값을 씁니다): {position_hint.strip()}')
    hint_block = ('\n' + '\n'.join(hints) + '\n') if hints else ''

    return f"""[요청]
아래는 사용자가 직접 붙여넣은 채용공고 본문입니다. skills/parse-job.md 절차대로 파싱해서
지시된 출력 형식(===JOB_MARKDOWN===/===REQUIRED_JSON===/===PREFERRED_JSON===/===META_JSON===)
그대로 출력하세요. 이 호출은 대화형 확인 없이 한 번에 끝나야 하므로, 회사명/직무명을 원문에서
찾을 수 없으면 아래 힌트를 쓰고, 힌트도 없으면 META_JSON 의 해당 필드를 빈 문자열로 두세요
(지어내지 마세요).
{hint_block}
--- templates/job-entry.md (이 섹션 구성을 JOB_MARKDOWN 에 그대로 따르세요) ---
{template}
---

--- 공고 원문 (붙여넣기) ---
{raw_text.strip()}
---
"""


def parse_llm_output(text: str):
    """블록을 파싱한다. 실패 사유를 문자열로 돌려준다 (성공 시 None)."""
    m_md = _MARKDOWN_BLOCK.search(text)
    m_req = _REQUIRED_BLOCK.search(text)
    m_pref = _PREFERRED_BLOCK.search(text)
    m_meta = _META_BLOCK.search(text)
    if not (m_md and m_req and m_pref and m_meta):
        return None, 'LLM 출력에서 필요한 블록을 찾지 못했습니다 (형식 오류).'

    try:
        required = json.loads(m_req.group('body'))
        preferred = json.loads(m_pref.group('body'))
        meta = json.loads(m_meta.group('body'))
    except json.JSONDecodeError as e:
        return None, f'JSON 파싱 실패: {e}'

    if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
        return None, 'REQUIRED_JSON 이 문자열 배열이 아닙니다.'
    if not isinstance(preferred, list) or not all(isinstance(x, str) for x in preferred):
        return None, 'PREFERRED_JSON 이 문자열 배열이 아닙니다.'
    if not isinstance(meta, dict):
        return None, 'META_JSON 이 객체가 아닙니다.'

    return {
        'markdown': m_md.group('body').strip(),
        'required': required,
        'preferred': preferred,
        'meta': meta,
    }, None


def _duplicate_result(existing: dict) -> JobResult:
    return JobResult(
        ok=True, duplicate=True, saved=True, job_id=existing['id'], source=existing['source'],
        source_job_id=existing['source_job_id'] or '', path=existing['raw_path'] or '',
        company=existing['company'], position=existing['position'], deadline=existing['deadline'],
        employment_type=existing['employment_type'],
        required=json.loads(existing['required_json']), preferred=json.loads(existing['preferred_json']),
        content_hash=existing['content_hash'] or '',
    )


def preview(raw_text: str, url: str = '', company_hint: str = '', position_hint: str = '',
            db_path=None, model=None) -> JobResult:
    """LLM 을 호출해 구조화만 한다 — DB/파일에는 아무것도 쓰지 않는다 (S4-3 이 이 결과를
    화면에 보여주고, 사용자가 확인/보정한 뒤 commit() 을 호출해 실제로 저장한다).

    이미 같은 내용(공백 정규화 후 해시 일치)의 공고가 있으면 LLM 을 호출하지 않고
    duplicate=True + saved=True 로 기존 행을 그대로 돌려준다 — 같은 공고를 실수로
    두 번 붙여넣어도 비용이 들지 않는다.
    """
    raw_text = raw_text or ''
    if len(raw_text.strip()) < MIN_RAW_LENGTH:
        return JobResult(ok=False, error=f'본문이 너무 짧습니다 ({MIN_RAW_LENGTH}자 이상 필요). URL 이 아니라 공고 본문을 붙여넣어주세요.')

    h = content_hash(raw_text)
    existing = find_by_hash(h, db_path)
    if existing:
        return _duplicate_result(existing)

    system = load_skill('parse-job')
    user_msg = build_prompt(raw_text, url=url, company_hint=company_hint, position_hint=position_hint)
    try:
        result = stream_complete(system=system, messages=[{'role': 'user', 'content': user_msg}],
                                  model=model, max_tokens=4096)
    except Exception as e:  # noqa: BLE001
        return JobResult(ok=False, error=f'LLM 호출 실패: {e}')

    parsed, err = parse_llm_output(result['text'])
    if err:
        return JobResult(ok=False, error=err)

    meta = parsed['meta']
    company = (meta.get('company') or company_hint or '').strip()
    position = (meta.get('position') or position_hint or '').strip()
    if not company or not position:
        return JobResult(ok=False, error='회사명 또는 직무명을 찾지 못했습니다. company_hint/position_hint 로 알려주세요.')

    deadline = meta.get('deadline')
    if deadline is not None and not _DEADLINE_RE.match(str(deadline)):
        deadline = None  # 형식이 이상하면 지어내지 말고 비워둔다

    return JobResult(
        ok=True, duplicate=False, saved=False, company=company, position=position,
        deadline=deadline, employment_type=meta.get('employment_type'),
        required=parsed['required'], preferred=parsed['preferred'], markdown=parsed['markdown'],
        raw_text=raw_text, content_hash=h, url=url.strip(),
    )


def commit(preview_result: JobResult, jobs_dir: Path = None, db_path=None) -> JobResult:
    """preview() 가 돌려준 결과를 실제로 jobs_dir/manual-NNNN.md + jobs 테이블에 쓴다.

    preview 와 commit 사이에 같은 공고가 이미 저장됐을 수 있으므로(같은 화면을 두 번
    제출하는 등) 여기서 content_hash 로 한 번 더 확인한다 — 이때는 duplicate=True 로
    기존 행을 돌려주고 새로 쓰지 않는다.
    """
    if not preview_result.ok or preview_result.duplicate:
        return preview_result  # 이미 실패했거나 이미 저장된 것 — 새로 할 일이 없다

    company = preview_result.company.strip()
    position = preview_result.position.strip()
    if not company or not position:
        return JobResult(ok=False, error='회사명 또는 직무명이 비어 있습니다.')

    h = preview_result.content_hash or content_hash(preview_result.raw_text)
    existing = find_by_hash(h, db_path)
    if existing:
        return _duplicate_result(existing)

    conn = db.get_conn(db_path)
    try:
        source_job_id = _next_manual_id(conn)
        filename = f'manual-{source_job_id}.md'
        conn.execute(
            'INSERT INTO jobs (source, source_job_id, company, position, deadline, employment_type, '
            'required_json, preferred_json, url, raw_path, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            ('manual', source_job_id, company, position, preview_result.deadline, preview_result.employment_type,
             json.dumps(preview_result.required, ensure_ascii=False),
             json.dumps(preview_result.preferred, ensure_ascii=False),
             preview_result.url or None, f'jobs/{filename}', h),
        )
        job_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        conn.commit()
    finally:
        conn.close()

    if jobs_dir is not None:
        jobs_dir.mkdir(parents=True, exist_ok=True)
        markdown = _normalize_markdown_meta(preview_result.markdown, 'manual', source_job_id)
        (jobs_dir / filename).write_text(markdown + '\n', encoding='utf-8')

    return JobResult(
        ok=True, duplicate=False, saved=True, job_id=job_id, source='manual', source_job_id=source_job_id,
        path=f'jobs/{filename}', company=company, position=position, deadline=preview_result.deadline,
        employment_type=preview_result.employment_type,
        required=preview_result.required, preferred=preview_result.preferred, content_hash=h,
    )


def parse_and_save(raw_text: str, url: str = '', company_hint: str = '', position_hint: str = '',
                    jobs_dir: Path = None, db_path=None, model=None) -> JobResult:
    """preview() + commit() 을 한 번에 — CLI/스킬 직접 실행처럼 확인 단계 없이 바로 저장할 때 쓴다.
    S4-3 웹 UI는 이 함수 대신 preview() 로 먼저 보여주고 commit() 으로 저장한다."""
    p = preview(raw_text, url=url, company_hint=company_hint, position_hint=position_hint,
                db_path=db_path, model=model)
    if not p.ok or p.duplicate:
        return p
    return commit(p, jobs_dir=jobs_dir, db_path=db_path)
