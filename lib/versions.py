"""문서 버전 보존 (ROADMAP S8-1). outputs/ 아래 문서를 편집기에서 저장할 때마다 이전
내용을 지우지 않고 새 버전으로 남긴다.

문제: `/output/<name>/<filename>/save` 는 지금까지 그냥 덮어썼다 — 이전 버전이 사라진다.

방식: 문서 "이력서.md" 를 저장하면, 그 옆에 "이력서/" 폴더를 만들고 그 안에 v1.md, v2.md, ...
로 각 저장 시점의 전체 내용을 쌓는다. "이력서.md" 자기 자신은 항상 최신 버전과 같은 내용으로
유지한다 — 재생성(lib/resume.py 등)이나 다른 화면이 그 평범한 경로를 계속 그대로 쓸 수 있게
하기 위해서다. 버전 각각은 SQLite `documents` 테이블에도 한 행씩 기록한다(사용자, 문서 종류,
버전 번호, 경로, 글자수) — 목록을 빠르게 조회하려는 용도고, 실제 내용은 항상 파일이 정본이다.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from lib import db

_VERSION_FILE = re.compile(r'^v(\d+)\.md$')

# 문서 파일명(확장자 뗀 것) -> documents.kind. 못 알아보는 이름(옛 플랫 자소서 파일 등)은
# 전부 'coverletter' 로 본다 — S1~S1-4 시절엔 이 폴더에 자소서만 있었으므로 안전한 기본값이다.
KIND_BY_STEM = {'이력서': 'resume', '자소서': 'coverletter', 'portfolio-outline': 'portfolio'}


def infer_kind(filename: str) -> str:
    return KIND_BY_STEM.get(Path(filename).stem, 'coverletter')


def version_dir(doc_path: Path) -> Path:
    """'.../이력서.md' -> '.../이력서/' (버전 파일이 쌓이는 형제 폴더)."""
    return doc_path.parent / doc_path.stem


def next_version_number(vdir: Path) -> int:
    if not vdir.exists():
        return 1
    nums = [int(m.group(1)) for f in vdir.glob('v*.md') for m in [_VERSION_FILE.match(f.name)] if m]
    return (max(nums) + 1) if nums else 1


@dataclass
class VersionInfo:
    version: int
    path: str            # output_dir(user) 기준 상대경로, 예: '기업_직무/이력서/v1.md'
    char_count: int
    created_at: str


@dataclass
class VersionResult:
    ok: bool
    version: int = 0
    path: str = ''        # output_dir(user) 기준 상대경로 — 방금 만든 버전 파일
    char_count: int = 0
    error: str = ''


def save_version(user_output_dir: Path, rel_doc_path: str, content: str,
                  user: str, db_path=None, run_id=None) -> VersionResult:
    """user_output_dir(=output_dir(name)) 아래 rel_doc_path(예: '기업_직무/이력서.md') 를
    저장하면서 새 버전을 함께 남긴다.

    항상 이 함수를 거쳐야 버전이 쌓인다 — 파일을 직접 write_text() 하면 기록 없이 덮어써진다.
    호출 전에 rel_doc_path 의 각 경로 조각은 호출자가 이미 안전성 검사(web.paths.safe_path)를
    끝냈다고 가정한다 — 이 함수 자체는 경로 조작 방어를 하지 않는다(웹 레이어의 책임).

    run_id 는 이 문서를 만든 실행의 식별자다(lib/runs.py). 손으로 저장하면 None 이고,
    None 이라는 사실 자체가 "공통 실행기를 거치지 않았다" 는 기록이 된다 (S11-17).
    """
    doc_path = user_output_dir / rel_doc_path
    vdir = version_dir(doc_path)
    vdir.mkdir(parents=True, exist_ok=True)
    version = next_version_number(vdir)
    version_file = vdir / f'v{version}.md'
    version_file.write_text(content, encoding='utf-8')

    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(content, encoding='utf-8')

    rel_version_path = str(version_file.relative_to(user_output_dir)).replace('\\', '/')
    char_count = len(content)
    kind = infer_kind(doc_path.name)

    conn = db.get_conn(db_path)
    try:
        conn.execute(
            'INSERT INTO documents (user, job_id, kind, version, path, char_count, run_id)'
            ' VALUES (?,?,?,?,?,?,?)',
            (user, None, kind, version, rel_version_path, char_count, run_id),
        )
        conn.commit()
    finally:
        conn.close()

    return VersionResult(ok=True, version=version, path=rel_version_path, char_count=char_count)


def list_versions(user_output_dir: Path, rel_doc_path: str, user: str, db_path=None) -> List[VersionInfo]:
    """documents 테이블에서 이 문서의 버전 이력을 오래된 순으로 돌려준다.

    kind + path 접두어(문서 폴더)로 좁힌다 — 같은 사용자가 만든 다른 문서와 섞이지 않게.
    """
    doc_path = user_output_dir / rel_doc_path
    vdir_rel = str(version_dir(doc_path).relative_to(user_output_dir)).replace('\\', '/')
    kind = infer_kind(doc_path.name)

    conn = db.get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT version, path, char_count, created_at FROM documents "
            "WHERE user = ? AND kind = ? AND path LIKE ? ORDER BY version ASC",
            (user, kind, f'{vdir_rel}/%'),
        ).fetchall()
        return [VersionInfo(version=r['version'], path=r['path'], char_count=r['char_count'],
                             created_at=r['created_at']) for r in rows]
    finally:
        conn.close()
