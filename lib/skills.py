"""스킬 파일 로더. skills/*.md 는 CLI 와 웹이 공유하는 프롬프트 템플릿이다 (ROADMAP 1-B).

절대 원칙: 스킬 파일을 수정하거나 파이썬 코드에 프롬프트를 하드코딩하지 않는다.
여기서는 파일을 그대로 읽어 문자열로 돌려주기만 한다.
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / 'skills'


class SkillNotFoundError(FileNotFoundError):
    """요청한 이름의 skills/*.md 가 없다."""


def list_skills():
    return sorted(p.stem for p in SKILLS_DIR.glob('*.md'))


def load_skill(name):
    """skills/[name].md 를 읽어 프롬프트 문자열로 반환한다.

    name 은 확장자 없이 준다 (예: 'write-coverletter').
    """
    if not name or not isinstance(name, str) or '/' in name or '\\' in name or name in ('.', '..'):
        raise ValueError(f'유효하지 않은 스킬 이름: {name!r}')
    path = SKILLS_DIR / f'{name}.md'
    if not path.is_file():
        raise SkillNotFoundError(f'스킬을 찾을 수 없습니다: {name} (사용 가능: {", ".join(list_skills())})')
    return path.read_text(encoding='utf-8')
