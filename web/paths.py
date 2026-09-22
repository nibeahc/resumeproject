"""경로 보안 헬퍼. 사용자 입력이 들어간 경로는 반드시 여기를 거친다.

새 라우트가 파일 경로를 만들 때는 PROJECT_ROOT / ... 를 직접 조립하지 말고
user_dir(), output_dir(), safe_path() 를 쓴다.
"""
import re
import unicodedata
from pathlib import Path

from flask import abort, current_app

ALLOWED_EXT = {'.hwpx', '.hwp', '.docx', '.pdf', '.md', '.txt'}

_BAD_SEGMENT = re.compile(r'[/\\\x00-\x1f]')
_BAD_FILENAME_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


def project_root() -> Path:
    return current_app.config['PROJECT_ROOT']


def valid_segment(value):
    """경로의 한 조각(사용자명, 파일명)으로 쓸 수 있는 문자열인가."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= 100
        and value not in ('.', '..')
        and not _BAD_SEGMENT.search(value)
    )


def safe_path(base: Path, *parts) -> Path:
    """base 밖으로 나가는 경로는 400. 각 조각도 단일 경로 조각이어야 한다."""
    if not all(valid_segment(x) for x in parts):
        abort(400)
    p = base.joinpath(*parts).resolve()
    if not p.is_relative_to(base.resolve()):
        abort(400)
    return p


def user_dir(name) -> Path:
    return safe_path(project_root() / 'users', name)


def output_dir(name) -> Path:
    return safe_path(project_root() / 'outputs', name)


def safe_upload_name(raw):
    """업로드 파일명 정리. secure_filename 은 한글을 지우므로 쓰지 않고,
    디렉터리 성분과 위험 문자만 제거한다. 결과가 비면 None."""
    name = unicodedata.normalize('NFC', raw or '')
    name = re.split(r'[/\\]', name)[-1]          # 디렉터리 성분 제거
    name = _BAD_FILENAME_CHARS.sub('', name).strip().lstrip('.')
    return name if valid_segment(name) else None
