"""자소서 작성 트렌드 리서치 (고도화, 2026-09-27). Anthropic API 의 web_search 서버 도구를
써서 최신 자소서/커버레터 작성 트렌드를 조사하고, references/trends/coverletter-trends.md 로
저장한다. write-coverletter.md 가 이 파일을 읽어 문단 작성에 참고한다.

**새 API 키가 필요 없다** — web_search 는 Anthropic 이 서버에서 직접 실행하는 도구라, 이미 쓰고
있는 ANTHROPIC_API_KEY 로 그대로 호출한다(검색 1000회당 $10 + 토큰 비용, 한 달에 한 번만
돌리므로 비용은 무시할 만하다). 워크넷/사람인처럼 별도 가입·승인 절차가 없다.

lib/llm.py 의 stream_complete() 를 재사용하지 않는 이유: 그 함수는 tools 파라미터를 받지
않고(자소서/이력서 등 기존 호출은 도구가 필요 없었다), 스트리밍 도중 서버 도구 호출/결과
블록을 다루지 않는다. 이 모듈은 비동기 스트리밍 진행 표시가 필요 없는 백그라운드 작업이라
client.messages.create() 를 직접, 논스트리밍으로 부른다.
"""
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from lib import config

DEFAULT_MAX_AGE_DAYS = 30  # 사용자 결정(2026-09-26): 한 달에 한 번이면 충분하다
DEFAULT_MAX_USES = 6       # 검색 횟수 상한 — 비용/시간 통제용

_UPDATED_LINE = re.compile(r'^> 최종 갱신일: (\d{4}-\d{2}-\d{2})', re.M)

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=config.anthropic_api_key())
    return _client


@dataclass
class TrendsResult:
    ok: bool
    markdown: str = ''                  # 저장용 전체 문서(헤더+본문+출처)
    sources: List[dict] = field(default_factory=list)  # [{'url', 'title'}, ...] 중복 제거됨
    error: str = ''


def _extract_text_and_citations(content_blocks):
    """web_search 응답의 content 블록에서 최종 텍스트와 인용 출처를 뽑는다.

    Anthropic 문서 그대로: content 는 text/server_tool_use/web_search_tool_result 블록이
    섞여 온다. 최종 답변에 해당하는 text 블록만 이어붙이고(검색 중간에 나오는 "~를
    검색하겠습니다" 같은 text 는 결과물에 섞이면 안 되므로, 실제로는 전부 이어붙이되 사람이
    읽었을 때 자연스럽도록 그대로 둔다 — Claude 가 검색 도구 사용 시 보통 중간 서술 없이
    바로 최종 텍스트를 내는 경향이 실측으로 확인됨), citations 는 텍스트 블록에 달려 있다.
    실측(2026-09-27)으로 확인된 문제: 검색을 여러 번 하면 검색 사이사이에 "~를 더 찾아보겠
    습니다" 같은 짧은 서술 text 블록이 낀다. 이걸 그대로 다 이어붙이면 저장되는 문서 맨 앞에
    그 서술이 섞여 나온다. 마지막 web_search_tool_result 블록 *이후에* 나온 text 블록만
    "진짜 최종 답변"으로 보고, 그 앞의 중간 서술은 버린다(검색을 아예 안 한 경우엔 전부 쓴다).
    """
    last_result_idx = max(
        (i for i, b in enumerate(content_blocks) if b.type == 'web_search_tool_result'),
        default=-1,
    )
    texts = []
    sources = []
    seen_urls = set()
    for i, block in enumerate(content_blocks):
        if block.type != 'text':
            continue
        for c in (block.citations or []):
            url = getattr(c, 'url', None)
            if url and url not in seen_urls:
                seen_urls.add(url)
                sources.append({'url': url, 'title': getattr(c, 'title', '') or url})
        if i > last_result_idx:  # 검색이 전부 끝난 뒤에 나온 text 만 최종 답변으로 채택
            texts.append(block.text)
    return '\n'.join(texts).strip(), sources


def _strip_leading_narration(body: str) -> str:
    """실측(2026-09-27)으로 확인된 잔여 문제: 검색이 전부 끝난 뒤의 "진짜 최종" text 블록
    조차, 그 블록 맨 앞에 "충분한 정보를 확보했습니다. 이제 정리하겠습니다." 같은 전환 문장이
    붙어 나올 때가 있다(블록 단위로는 못 거른다 — 같은 블록 안이라서). 프롬프트가 "## 로 항목
    구분"을 요구했으므로, 실제 내용은 항상 마크다운 헤딩(#)으로 시작한다고 보고 그 앞의
    서술을 잘라낸다. 헤딩이 아예 없으면(모델이 형식을 안 지킨 드문 경우) 손대지 않는다."""
    m = re.search(r'^#{1,3} ', body, re.M)
    return body[m.start():] if m and m.start() > 0 else body


def research_trends(model=None, max_uses=DEFAULT_MAX_USES) -> TrendsResult:
    """web_search 도구로 최신 자소서 작성 트렌드를 조사해 저장용 마크다운을 만든다."""
    today = date.today().strftime('%Y-%m-%d')
    prompt = f"""오늘은 {today}입니다. 최근(최근 6개월~1년 이내) 한국 채용시장의 자기소개서(자소서)
작성 트렌드를 웹 검색으로 조사하고 정리해주세요. 아래 항목을 다루세요:

1. 채용담당자/인사담당자가 최근 선호하는 자소서 구조(예: 두괄식, STAR 기법 활용 등)
2. 최근 지양해야 한다고 지적되는 클리셰 표현이나 상투적 문구
3. AI(챗GPT 등)로 작성한 티가 난다고 지적되는 문장 특징과, 그것을 피하는 방법
4. 최근 채용시장에서 강조되는 역량 표현 방식(예: 정량적 성과 강조, 직무 연관성 강조 등)
5. 그 외 최근 주목할 만한 자소서 작성 조언

각 항목은 실제 검색 결과에 근거해서만 쓰고, 근거 없는 일반론을 지어내지 마세요. 검색으로
확인되지 않는 항목은 "최근 관련 정보를 찾지 못함"이라고 정직하게 쓰세요. 한국어로,
마크다운 형식(## 로 항목 구분)으로 정리해주세요."""

    try:
        response = _get_client().messages.create(
            model=model or config.model(),
            max_tokens=8192,
            thinking={'type': 'disabled'},  # lib/llm.py 와 같은 이유 — thinking 이 켜지면 max_tokens 를 다 먹을 수 있다
            messages=[{'role': 'user', 'content': prompt}],
            tools=[{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': max_uses}],
        )
    except Exception as e:  # noqa: BLE001
        return TrendsResult(ok=False, error=f'API 호출 실패: {e}')

    # 실측(2026-09-27)으로 발견: max_tokens=2048 이었을 때 5개 항목 + 인용을 다 쓰기 전에
    # 출력이 잘렸는데, 잘린 문장이 그대로 저장되어 화면에 "쉼표나 별표 같은 기" 처럼 문장이
    # 끊긴 채 노출됐다. 잘린 결과를 조용히 저장하지 않고 실패로 취급한다 — 이 프로젝트의
    # "코드가 검증할 수 있으면 LLM 의 자체 보고를 믿지 않는다" 원칙 그대로(다른 잘림도 이
    # 방식으로 잡힌다. max_tokens 를 8192 로 올린 것과는 별개의 안전장치).
    if response.stop_reason == 'max_tokens':
        return TrendsResult(ok=False, error='응답이 max_tokens 에서 잘렸습니다 (내용 미완성, 저장하지 않음).')

    body, sources = _extract_text_and_citations(response.content)
    body = _strip_leading_narration(body)
    if not body:
        return TrendsResult(ok=False, error='검색 결과에서 텍스트를 얻지 못했습니다.')

    sources_block = '\n'.join(f'- [{s["title"]}]({s["url"]})' for s in sources) or '(인용된 출처 없음)'
    markdown = f"""# 자소서 작성 트렌드 (자동 리서치)

> 최종 갱신일: {today}
> 이 문서는 lib/trends.py 가 Anthropic web_search 도구로 자동 조사해 만들었습니다.
> 한 달에 한 번 자동 갱신됩니다(직접 수정해도 다음 갱신 때 덮어써집니다).

{body}

## 출처

{sources_block}
"""
    return TrendsResult(ok=True, markdown=markdown, sources=sources)


# ---------------------------------------------------------------------------
# 저장소 구조 (S11-6, 설계검토 F-1)
#
# 예전엔 references/trends/coverletter-trends.md 한 파일이었고, 한 달에 한 번
# 자동으로 **덮어써졌다.** 그 파일이 그대로 생성 프롬프트에 들어갔으므로, 아무도 읽지
# 않은 검색 결과가 다음 달부터 작성 정책이 되는 구조였다. 검토 F-1 의 지적이 이것이다.
#
#   references/trends/
#     raw/YYYY-MM-DD.md    수집 결과. 자동 갱신은 여기에만 쓴다
#     approved.md          사람이 검토해 승인한 내용. **프롬프트에 들어가는 유일한 파일**
#     history/             이전 승인본 (되돌릴 수 있게)
#
# 자동 갱신이 approved.md 를 건드리지 못한다는 것이 이 구조의 전부다.
# ---------------------------------------------------------------------------

_LEGACY_NAME = 'coverletter-trends.md'
_RAW_NAME = re.compile(r'^(\d{4}-\d{2}-\d{2})\.md$')
_APPROVED_LINE = re.compile(r'^> 승인일: (\d{4}-\d{2}-\d{2})', re.M)


def raw_dir(base: Path) -> Path:
    return base / 'raw'


def approved_path(base: Path) -> Path:
    return base / 'approved.md'


def history_dir(base: Path) -> Path:
    return base / 'history'


def migrate_legacy(base: Path) -> Optional[Path]:
    """예전의 단일 파일을 raw/ 로 옮긴다. 승인본으로 올리지는 **않는다** —
    그 파일은 사람이 검토한 적 없는 수집 결과이고, 검토 없이 정책이 되는 것을 막는 게
    이 변경의 목적이기 때문이다. 옮긴 경로를 돌려준다(할 일이 없으면 None).

    여러 번 불러도 안전하다. create_app() 에서 한 번 부른다.
    """
    legacy = base / _LEGACY_NAME
    if not legacy.exists():
        return None
    m = _UPDATED_LINE.search(legacy.read_text(encoding='utf-8'))
    stamp = m.group(1) if m else date.today().isoformat()
    target = raw_dir(base) / f'{stamp}.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        legacy.unlink()          # 이미 옮긴 적이 있다
        return target
    legacy.rename(target)
    return target


def list_raw(base: Path) -> List[Path]:
    """수집본을 최신순으로. 파일명이 YYYY-MM-DD 라 사전순 정렬이 곧 날짜순이다."""
    d = raw_dir(base)
    if not d.exists():
        return []
    return sorted((f for f in d.glob('*.md') if _RAW_NAME.match(f.name)), reverse=True)


def latest_raw(base: Path) -> Optional[Path]:
    files = list_raw(base)
    return files[0] if files else None


def load_raw(base: Path) -> Optional[str]:
    """가장 최근 수집본. 검토 화면이 승인본과 나란히 보여준다."""
    f = latest_raw(base)
    return f.read_text(encoding='utf-8') if f else None


def load_trends(base: Path) -> Optional[str]:
    """**프롬프트에 들어가는 유일한 경로.** 승인본만 읽는다.
    승인된 게 없으면 None 이고, build_prompt 가 "아직 조사된 트렌드 없음" 으로 처리한다.
    """
    path = approved_path(base)
    return path.read_text(encoding='utf-8') if path.exists() else None


def last_updated(base: Path) -> Optional[date]:
    """마지막 **수집** 일자. 자동 갱신 주기 판단에 쓴다."""
    f = latest_raw(base)
    if f is None:
        return None
    m = _UPDATED_LINE.search(f.read_text(encoding='utf-8'))
    if m:
        return datetime.strptime(m.group(1), '%Y-%m-%d').date()
    name_m = _RAW_NAME.match(f.name)       # 본문 헤더가 없으면 파일명으로 되돌아간다
    return datetime.strptime(name_m.group(1), '%Y-%m-%d').date() if name_m else None


def last_approved(base: Path) -> Optional[date]:
    path = approved_path(base)
    if not path.exists():
        return None
    m = _APPROVED_LINE.search(path.read_text(encoding='utf-8'))
    return datetime.strptime(m.group(1), '%Y-%m-%d').date() if m else None


def has_pending_review(base: Path) -> bool:
    """검토를 기다리는 수집본이 있는가 — 승인본이 없거나, 수집이 승인보다 최근이면."""
    collected = last_updated(base)
    if collected is None:
        return False
    approved = last_approved(base)
    return approved is None or collected > approved


def needs_refresh(base: Path, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> bool:
    updated = last_updated(base)
    if updated is None:
        return True
    return (date.today() - updated) > timedelta(days=max_age_days)


def save_trends(result: TrendsResult, base: Path) -> Path:
    """수집 결과를 raw/ 에 남긴다. approved.md 는 건드리지 않는다 — 이게 핵심이다."""
    target = raw_dir(base) / f'{date.today().isoformat()}.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result.markdown, encoding='utf-8')
    return target


def approve(base: Path, content: Optional[str] = None) -> Optional[Path]:
    """사람이 검토한 내용을 승인본으로 올린다. content 를 안 주면 최신 수집본 전체를 쓴다
    (항목을 추리거나 고쳤으면 그 결과를 content 로 넘긴다).

    기존 승인본은 history/ 로 옮겨 되돌릴 수 있게 한다.
    """
    if content is None:
        content = load_raw(base)
    if not content or not content.strip():
        return None

    current = approved_path(base)
    if current.exists():
        hist = history_dir(base)
        hist.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        (hist / f'approved-{stamp}.md').write_text(current.read_text(encoding='utf-8'),
                                                    encoding='utf-8')

    today = date.today().isoformat()
    if not _APPROVED_LINE.search(content):
        content = f'> 승인일: {today}\n> 검토: 사용자\n\n{content.lstrip()}'
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(content, encoding='utf-8')
    return current


def ensure_fresh(base: Path, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
                  max_uses: int = DEFAULT_MAX_USES, model=None) -> Optional[TrendsResult]:
    """오래됐을 때만 실제로 리서치한다(비용 통제). 갱신을 안 했으면 None 을 돌려준다 —
    "이미 최신이라 아무 것도 안 했다"와 "리서치했다"를 호출자가 구분할 수 있게."""
    if not needs_refresh(base, max_age_days):
        return None
    result = research_trends(model=model, max_uses=max_uses)
    if result.ok:
        save_trends(result, base)
    return result


_refresh_lock = threading.Lock()
_refreshing = False  # 이 프로세스 안에서 지금 백그라운드 리서치가 진행 중인가


def trigger_background_refresh(base: Path, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
                                max_uses: int = DEFAULT_MAX_USES, model=None) -> bool:
    """"앱 자체가 자동으로 주기적으로 검색"(사용자 요청, 2026-09-27)의 실제 구현. 오래됐으면
    페이지 로딩을 막지 않고 백그라운드 스레드에서 조용히 갱신한다.

    개인용 로컬 앱이라 별도 스케줄러(cron, APScheduler 등)를 새로 두지 않았다 — 어차피 앱이
    꺼져 있으면 스케줄러도 못 돈다. 대신 사용자가 실제로 페이지를 열 때(=앱을 쓸 때)마다 이
    함수를 부르면, "한 달에 한 번"이라는 주기가 자연스럽게 지켜진다.

    여러 페이지 로드가 겹쳐서 API 를 중복 호출하지 않도록 프로세스 안에서 플래그로 막는다
    (개인용 단일 프로세스를 전제하므로 이 정도로 충분하다 — 여러 프로세스가 동시에 뜨는
    배포 환경이었다면 파일 락 등이 필요했을 것).

    S11-6 이후로 이 경로는 raw/ 에만 쓴다. 승인본은 사람이 올린다.

    반환값은 "이번 호출로 백그라운드 작업을 새로 띄웠는가" — 이미 진행 중이거나 아직
    갱신할 때가 안 됐으면 False.
    """
    if not needs_refresh(base, max_age_days):
        return False

    global _refreshing
    with _refresh_lock:
        if _refreshing:
            return False
        _refreshing = True

    def worker():
        global _refreshing
        try:
            ensure_fresh(base, max_age_days=max_age_days, max_uses=max_uses, model=model)
        except Exception:  # noqa: BLE001 — 백그라운드 작업 실패로 페이지 요청을 망가뜨리지 않는다
            pass
        finally:
            with _refresh_lock:
                _refreshing = False

    threading.Thread(target=worker, daemon=True).start()
    return True
