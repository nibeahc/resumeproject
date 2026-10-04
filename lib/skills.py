"""스킬 파일 로더. skills/*.md 는 CLI 와 웹이 공유하는 프롬프트 템플릿이다 (ROADMAP 1-B).

절대 원칙: 스킬 파일을 수정하거나 파이썬 코드에 프롬프트를 하드코딩하지 않는다.

S11-15 (설계검토 A-3): 스킬 파일 맨 앞에 **기계 판독 계약**(frontmatter)을 둘 수 있다.
숫자와 필수조건의 원본을 계약에 두고, 코드와 프롬프트가 같은 값을 각각 손으로 유지하는
상황을 없애기 위해서다.

    ---
    contract_version: 1
    experience_policy:
      max_count: 2
    ---

    # 자소서 작성          ← 여기부터가 프롬프트 본문

load_skill() 은 **본문만** 돌려준다. 계약은 load_frontmatter() 로 따로 읽는다.
이렇게 나누지 않으면 YAML 이 그대로 system 프롬프트에 실려 모델에게 간다.
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / 'skills'

_FRONTMATTER = re.compile(r'\A---[ \t]*\n(?P<body>.*?)\n---[ \t]*\n?', re.S)
_KEY_VALUE = re.compile(r'^(?P<indent>[ \t]*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)[ \t]*:[ \t]*(?P<value>.*?)[ \t]*$')


class SkillNotFoundError(FileNotFoundError):
    """요청한 이름의 skills/*.md 가 없다."""


class ContractError(ValueError):
    """계약(frontmatter)의 형식이 잘못됐다.

    **조용히 무시하지 않는다.** 알 수 없는 필드나 지원하지 않는 버전을 넘기면
    코드는 기본값으로 돌아가는데, 스킬 파일에 적힌 정책은 지켜지는 줄 알게 된다
    (설계검토 A-3).
    """


def list_skills():
    return sorted(p.stem for p in SKILLS_DIR.glob('*.md'))


def _scalar(raw: str, where: str):
    """계약에 쓰는 값은 세 가지뿐이다 — 정수, 불리언, 문자열."""
    v = raw.strip()
    if v.startswith(('"', "'")) and len(v) >= 2 and v[-1] == v[0]:
        return v[1:-1]
    low = v.lower()
    if low in ('true', 'yes'):
        return True
    if low in ('false', 'no'):
        return False
    if re.fullmatch(r'-?\d+', v):
        return int(v)
    if not v:
        raise ContractError(f'{where}: 값이 비어 있습니다')
    return v


def parse_frontmatter(text: str):
    """(계약 dict, 본문). frontmatter 가 없으면 ({}, 원문 전체).

    지원하는 형태는 한 단계 중첩까지다. 리스트나 여러 줄 문자열은 쓰지 않는다 —
    계약에 들어갈 값은 임계값과 필수조건뿐이고, 설명은 본문에 쓴다.
    """
    m = _FRONTMATTER.match(text or '')
    if not m:
        return {}, text

    data, section = {}, None
    for lineno, line in enumerate(m.group('body').split('\n'), start=2):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        kv = _KEY_VALUE.match(line)
        if not kv:
            raise ContractError(f'{lineno}행을 읽을 수 없습니다: {line.strip()!r}')
        key, raw, indented = kv.group('key'), kv.group('value'), bool(kv.group('indent'))
        where = f'{lineno}행 {key}'
        if indented:
            if section is None:
                raise ContractError(f'{where}: 상위 항목 없이 들여쓰기됐습니다')
            data[section][key] = _scalar(raw, where)
        elif raw == '':
            section = key
            data[section] = {}
        else:
            section = None
            data[key] = _scalar(raw, where)
    return data, text[m.end():]


def load_frontmatter(name):
    """계약만 읽는다. frontmatter 가 없으면 빈 dict."""
    return parse_frontmatter(_read(name))[0]


def load_skill(name):
    """skills/[name].md 의 **프롬프트 본문**을 반환한다 (계약은 떼어낸다).

    name 은 확장자 없이 준다 (예: 'write-coverletter').
    """
    return parse_frontmatter(_read(name))[1]


def _read(name):
    if not name or not isinstance(name, str) or '/' in name or '\\' in name or name in ('.', '..'):
        raise ValueError(f'유효하지 않은 스킬 이름: {name!r}')
    path = SKILLS_DIR / f'{name}.md'
    if not path.is_file():
        raise SkillNotFoundError(f'스킬을 찾을 수 없습니다: {name} (사용 가능: {", ".join(list_skills())})')
    return path.read_text(encoding='utf-8')
