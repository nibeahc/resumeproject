"""공고 원문 가져오기 (S11-16).

`Claude.md` 의 **"외부 자료 수집 정책"** 4단계를 코드로 옮긴 것이다.
예전에는 "채용 사이트 스크래핑 금지" 한 줄이라 공개 URL 한 건을 읽는 것도, 로그인을
우회해 대량으로 긁는 것도 똑같이 금지였다. S11-0 에서 정책을 단계로 나눴고, 여기서
그 단계를 실제 코드 경로로 만든다.

  기본 허용   사용자가 붙여넣은 본문, 업로드한 파일(extract_file)
  조건부 허용 사이트 정책을 확인한 공개 URL 단건 수집(fetch_url)
  금지        이용약관이 재가공을 금지한 사이트, robots.txt 가 막은 경로,
              로그인·캡차 뒤의 페이지, 사설망 주소
  실패 시     추론하지 않고 붙여넣기나 파일 업로드를 요청한다

**이 모듈은 한 번에 한 URL 만 가져온다.** 목록을 돌며 수집하는 기능은 넣지 않았다 —
그건 정책의 "허가되지 않은 반복·대량 수집" 이다.
"""
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

USER_AGENT = 'ResumeProject/1.0 (personal job-application assistant; single-page fetch)'
TIMEOUT_SECONDS = 10
MAX_BYTES = 2_000_000
MIN_TEXT_LENGTH = 200       # 이보다 짧으면 자바스크립트로 그리는 페이지일 가능성이 높다
MIN_INTERVAL_SECONDS = 3    # 같은 호스트 연속 요청 간격

# 이용약관이 재가공을 명시적으로 금지하는 사이트. 정책의 "금지" 구간이라 받기 전에 막는다.
# 근거 문구를 함께 적는다 — 나중에 왜 막았는지 추적할 수 있어야 한다.
BLOCKED_HOSTS = {
    'jobkorea.co.kr': '잡코리아는 공고 하단에 "본 채용정보는 잡코리아의 동의없이 무단전재, '
                      '재배포, 재가공할 수 없으며" 라고 명시합니다.',
    'saramin.co.kr': '사람인은 이용약관에서 콘텐츠의 무단 수집과 재가공을 금지합니다. '
                     '공식 API 를 쓰거나 본문을 붙여넣어 주세요.',
    'wanted.co.kr': '원티드는 이용약관에서 자동화된 수집을 금지합니다.',
    'incruit.com': '인크루트는 이용약관에서 콘텐츠 무단 복제와 재가공을 금지합니다.',
}

_last_request_at = {}


@dataclass
class FetchResult:
    ok: bool
    text: str = ''
    url: str = ''
    fetched_at: str = ''
    error: str = ''
    # 막힌 이유를 화면이 구분할 수 있게 한다. blocked 면 "붙여넣기/업로드" 를 안내한다.
    reason: str = ''          # blocked | robots | network | empty | invalid
    hint: str = ''
    meta: dict = field(default_factory=dict)


class _TextExtractor(HTMLParser):
    """HTML 에서 본문 텍스트만. bs4 를 쓰지 않는 이유는 의존성을 늘리지 않기 위해서다.
    공고는 표와 문단 위주라 태그를 걷어내는 정도로 충분하고, 사용자가 미리보기에서
    확인한 뒤 저장하므로 완벽한 추출일 필요도 없다."""

    SKIP = {'script', 'style', 'noscript', 'svg', 'head'}
    BREAK = {'p', 'div', 'br', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'td', 'th', 'section'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BREAK:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BREAK:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())

    def text(self):
        joined = ' '.join(self.parts)
        joined = re.sub(r'[ \t]+', ' ', joined)
        joined = re.sub(r'\s*\n\s*', '\n', joined)
        return re.sub(r'\n{3,}', '\n\n', joined).strip()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 — 깨진 HTML 로 요청 전체를 실패시키지 않는다
        pass
    return parser.text()


def _registrable(host: str) -> str:
    """blog.jobkorea.co.kr 도 jobkorea.co.kr 로 본다."""
    parts = (host or '').lower().lstrip('.').split('.')
    return '.'.join(parts[-3:]) if len(parts) >= 3 and parts[-2] in ('co', 'or', 'go', 'ne') \
        else '.'.join(parts[-2:])


def check_policy(url: str) -> Optional[FetchResult]:
    """가져오기 전에 정책으로 거를 수 있는 것. 통과면 None."""
    parsed = urllib.parse.urlparse(url or '')
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return FetchResult(ok=False, url=url, reason='invalid',
                           error='http 또는 https 주소만 가져올 수 있습니다.')

    host = parsed.hostname.lower()
    # 사설망과 루프백은 막는다. 사용자가 준 주소라도 내부 서비스를 찌르게 둘 이유가 없다.
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return FetchResult(ok=False, url=url, reason='blocked',
                                   error='사설망 주소는 가져올 수 없습니다.')
    except (socket.gaierror, ValueError):
        return FetchResult(ok=False, url=url, reason='network',
                           error=f'주소를 찾을 수 없습니다: {host}')

    blocked = BLOCKED_HOSTS.get(_registrable(host))
    if blocked:
        return FetchResult(
            ok=False, url=url, reason='blocked', error=blocked,
            hint='공고 본문을 복사해 붙여넣거나, 공고를 PDF 로 저장해 업로드해 주세요.')
    return None


def _robots_allows(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    robots_url = f'{parsed.scheme}://{parsed.netloc}/robots.txt'
    try:
        req = urllib.request.Request(robots_url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            body = resp.read(MAX_BYTES).decode('utf-8', 'replace')
    except Exception:  # noqa: BLE001
        # robots.txt 가 없거나 못 읽으면 막지 않는다. 표준 해석이 그렇다.
        return True
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(body.splitlines())
    return rp.can_fetch(USER_AGENT, url)


def fetch_url(url: str, *, opener=None, sleep=time.sleep) -> FetchResult:
    """공개 URL 한 건을 가져와 본문 텍스트로 돌려준다 (정책의 "조건부 허용").

    opener 와 sleep 은 테스트가 주입한다 — 테스트가 실제 네트워크를 타면 안 된다.
    """
    refused = check_policy(url)
    if refused:
        return refused

    if opener is None and not _robots_allows(url):
        return FetchResult(ok=False, url=url, reason='robots',
                           error='이 사이트의 robots.txt 가 해당 경로 수집을 허용하지 않습니다.',
                           hint='공고 본문을 붙여넣거나 PDF 로 저장해 업로드해 주세요.')

    host = urllib.parse.urlparse(url).hostname or ''
    waited = _last_request_at.get(host)
    if waited is not None:
        gap = time.monotonic() - waited
        if gap < MIN_INTERVAL_SECONDS:
            sleep(MIN_INTERVAL_SECONDS - gap)
    _last_request_at[host] = time.monotonic()

    try:
        if opener is not None:
            html = opener(url)
        else:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                charset = resp.headers.get_content_charset() or 'utf-8'
                html = resp.read(MAX_BYTES).decode(charset, 'replace')
    except urllib.error.HTTPError as e:
        hint = ('로그인이 필요한 페이지로 보입니다. 로그인 뒤 본문을 붙여넣어 주세요.'
                if e.code in (401, 403) else '본문을 붙여넣거나 파일로 업로드해 주세요.')
        return FetchResult(ok=False, url=url, reason='network',
                           error=f'가져오지 못했습니다 (HTTP {e.code})', hint=hint)
    except Exception as e:  # noqa: BLE001
        return FetchResult(ok=False, url=url, reason='network',
                           error=f'가져오지 못했습니다: {e}',
                           hint='본문을 붙여넣거나 파일로 업로드해 주세요.')

    text = html_to_text(html)
    if len(text) < MIN_TEXT_LENGTH:
        return FetchResult(
            ok=False, url=url, reason='empty',
            error=f'본문을 거의 읽지 못했습니다 ({len(text)}자).',
            hint='자바스크립트로 그리는 페이지일 수 있습니다. 본문을 붙여넣거나 '
                 'PDF 로 저장해 업로드해 주세요.')

    return FetchResult(ok=True, text=text, url=url,
                       fetched_at=datetime.now().isoformat(timespec='seconds'),
                       meta={'chars': len(text), 'method': 'url'})


def extract_file(path) -> FetchResult:
    """업로드한 파일에서 본문을 뽑는다 (정책의 "기본 허용").

    lib/extract.py 를 그대로 쓴다 — raw/ 업로드가 이미 pdf, docx, hwp, hwpx, md, txt 를
    처리한다. 공고를 위해 같은 코드를 다시 만들 이유가 없다.
    """
    from lib import extract as extract_module

    path = Path(path)
    r = extract_module.extract(path)
    if not r.ok:
        return FetchResult(ok=False, reason='empty', error=r.error,
                           hint='다른 형식으로 저장하거나 본문을 붙여넣어 주세요.')
    return FetchResult(ok=True, text=r.text,
                       fetched_at=datetime.now().isoformat(timespec='seconds'),
                       meta={'chars': len(r.text), 'method': r.method, 'filename': path.name})
