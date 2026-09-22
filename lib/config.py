"""설정 로더. .env 와 환경변수에서 키를 읽는다.

우선순위: 셸 환경변수 > .env > 기본값.
키가 필요한 기능은 require() 를 호출한다. 없으면 무엇이 없는지, 어디에 적는지,
어디서 발급받는지를 알려주는 ConfigError 가 발생한다.

    python3 -m lib.config    # 현재 설정 상태 점검. 필수 키가 없으면 종료 코드 1
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / '.env'

DEFAULT_MODEL = 'claude-sonnet-5'

# name: (필수 여부, 설명, 발급처)
KEYS = {
    'ANTHROPIC_API_KEY': (True, 'Anthropic API 키 (자소서 생성, 파싱, 매칭에 필요)',
                          'https://console.anthropic.com/settings/keys'),
    'WORKNET_API_KEY':   (False, '워크넷(고용24) 채용 API 키 (공고 탐색에만 필요)',
                          'https://www.data.go.kr/data/3038225/openapi.do'),
    'SARAMIN_API_KEY':   (False, '사람인 채용 API 키 (공고 탐색에만 필요)',
                          'https://oapi.saramin.co.kr/introduce'),
}


class ConfigError(RuntimeError):
    """설정이 없거나 잘못됐다. 메시지는 사용자에게 그대로 보여줄 수 있다."""


def parse_env(text):
    """KEY=VALUE 줄을 dict 로. 빈 줄, # 주석, export 접두사, 따옴표를 처리한다."""
    result = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        if '=' not in line:
            raise ConfigError(f'.env {lineno}번째 줄을 읽을 수 없습니다 (KEY=VALUE 형식이 아님): {raw!r}')
        key, _, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        elif ' #' in value:                      # 따옴표 없는 값 뒤의 인라인 주석
            value = value.split(' #', 1)[0].rstrip()
        result[key] = value
    return result


def load_env(path=None):
    """.env 를 읽어 os.environ 에 넣는다. 이미 있는 환경변수는 덮어쓰지 않는다.

    파일이 없으면 아무것도 하지 않는다. 읽은 키 이름 목록을 돌려준다.
    """
    p = Path(path) if path else ENV_PATH
    if not p.is_file():
        return []
    loaded = []
    for key, value in parse_env(p.read_text(encoding='utf-8')).items():
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


load_env()


def get(name, default=None):
    """값을 돌려준다. 비어 있으면 default."""
    value = os.environ.get(name, '').strip()
    return value or default


def require(name):
    """필수 값을 돌려준다. 없으면 무엇을 어떻게 해야 하는지 담은 ConfigError."""
    value = get(name)
    if value:
        return value
    _, desc, url = KEYS.get(name, (True, name, None))
    lines = [f'{name} 이(가) 설정되지 않았습니다.', f'  용도: {desc}']
    if url:
        lines.append(f'  발급: {url}')
    lines.append(f'  설정: {ENV_PATH} 에 `{name}=값` 을 적거나 셸에서 export 하세요.')
    if not ENV_PATH.is_file():
        lines.append('        .env 파일이 아직 없습니다.  cp .env.example .env  로 만드세요.')
    raise ConfigError('\n'.join(lines))


def require_or_exit(name):
    """CLI 스크립트용. 없으면 메시지를 stderr 에 찍고 종료 코드 1 로 끝낸다."""
    try:
        return require(name)
    except ConfigError as e:
        print(f'설정 오류: {e}', file=sys.stderr)
        raise SystemExit(1)


def anthropic_api_key():
    return require('ANTHROPIC_API_KEY')


def model():
    return get('MODEL', DEFAULT_MODEL)


def db_path():
    """SQLite 파일 경로. 상대경로는 프로젝트 루트 기준, ~ 는 홈으로 펼친다."""
    raw = get('DB_PATH')
    if not raw:
        return PROJECT_ROOT / 'data' / 'app.db'
    p = Path(raw).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


def hwp5txt_path():
    """hwp5txt 실행 파일 경로. 지정하지 않으면 None (lib/extract.py 가 PATH 와 자체 폴백을 시도한다)."""
    raw = get('HWP5TXT_PATH')
    return Path(raw).expanduser() if raw else None


def mask(value):
    """화면 출력용. 키 앞 6자와 뒤 2자만 남긴다."""
    return value if len(value) <= 12 else f'{value[:6]}...{value[-2:]}'


def check():
    """설정 상태를 출력한다. 필수 키가 하나라도 없으면 False."""
    print(f'.env: {ENV_PATH} ({"있음" if ENV_PATH.is_file() else "없음"})')
    ok = True
    for name, (required, desc, _) in KEYS.items():
        value = get(name)
        tag = '필수' if required else '선택'
        if value:
            print(f'  OK    {name:<18} [{tag}] {mask(value)}')
        elif required:
            print(f'  FAIL  {name:<18} [{tag}] 없음 - {desc}')
            ok = False
        else:
            print(f'  --    {name:<18} [{tag}] 없음 - {desc}')
    print(f'  MODEL       {model()}' + ('' if get('MODEL') else ' (기본값)'))
    print(f'  DB_PATH     {db_path()}' + ('' if get('DB_PATH') else ' (기본값)'))
    hwp = hwp5txt_path()
    print(f'  HWP5TXT_PATH {hwp}' if hwp else '  HWP5TXT_PATH (미설정 — lib/extract.py 가 PATH 와 자체 폴백을 시도)')
    return ok


if __name__ == '__main__':
    try:
        raise SystemExit(0 if check() else 1)
    except ConfigError as e:
        print(f'설정 오류: {e}', file=sys.stderr)
        raise SystemExit(1)
