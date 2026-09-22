"""raw/ 파일에서 텍스트를 추출한다 (ROADMAP S2-1).

parse-experiences.md Step 0-B 는 지금까지 "Claude 가 대화 중에 직접 이 변환을 수행하라"는
글로 적힌 지시였다. 대화 밖(웹 요청)에서는 그 지시를 아무도 실행하지 않으므로 실패해도 조용히
넘어갔다. 이 모듈은 그 변환을 코드로 옮기고, 성공과 실패를 항상 명시적으로 반환한다.

web/routes/parse.py 의 extract_text() 는 이 모듈의 얇은 래퍼로 바뀌었다 (중복 제거).

    python3 -m lib.extract users/채빈/raw   # 완료 조건 확인용 CLI
"""
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MIN_CHARS = 100

# 이 프로젝트(채빈님 맥북)에 pyhwp 가 설치된 위치. .env 의 HWP5TXT_PATH 와 PATH 둘 다 못 찾을 때 쓰는
# 최후 폴백이다. 다른 컴퓨터에서 쓸 때는 .env 에 HWP5TXT_PATH 를 지정하면 이 값은 무시된다.
HWP5TXT_FALLBACK = Path('/Users/chaebin/Library/Python/3.9/bin/hwp5txt')


@dataclass
class ExtractResult:
    ok: bool
    text: str
    error: Optional[str]
    method: str


def _too_short(method: str) -> ExtractResult:
    return ExtractResult(False, '', f'추출된 텍스트가 {MIN_CHARS}자 미만 (내용 없음 또는 형식 문제)', method)


def extract_md_txt(path: Path) -> ExtractResult:
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError as e:
        return ExtractResult(False, '', f'파일을 읽을 수 없음: {e}', 'text')
    if len(text) < MIN_CHARS:
        return _too_short('text')
    return ExtractResult(True, text, None, 'text')


def _extract_zip_xml(path: Path, member: str, pattern: str, method: str) -> ExtractResult:
    try:
        with zipfile.ZipFile(path, 'r') as z:
            xml = z.read(member).decode('utf-8', errors='replace')
    except (zipfile.BadZipFile, KeyError, FileNotFoundError, OSError) as e:
        return ExtractResult(False, '', f'{path.suffix} 구조를 읽을 수 없음: {e}', method)
    text = '\n'.join(t for t in re.findall(pattern, xml) if t.strip())
    if len(text) < MIN_CHARS:
        return _too_short(method)
    return ExtractResult(True, text, None, method)


def extract_docx(path: Path) -> ExtractResult:
    return _extract_zip_xml(path, 'word/document.xml', r'<w:t[^>]*>([^<]*)</w:t>', 'docx-xml')


def extract_hwpx(path: Path) -> ExtractResult:
    return _extract_zip_xml(path, 'Contents/section0.xml', r'<hp:t[^>]*>([^<]*)</hp:t>', 'hwpx-xml')


def find_hwp5txt() -> Optional[Path]:
    """우선순위: .env 의 HWP5TXT_PATH > PATH 에서 찾은 hwp5txt > 이 프로젝트 개발 환경의 고정 경로."""
    from lib import config
    configured = config.hwp5txt_path()
    if configured and configured.exists():
        return configured
    found = shutil.which('hwp5txt')
    if found:
        return Path(found)
    return HWP5TXT_FALLBACK if HWP5TXT_FALLBACK.exists() else None


def extract_hwp(path: Path) -> ExtractResult:
    exe = find_hwp5txt()
    if not exe:
        return ExtractResult(False, '', 'hwp5txt 를 찾을 수 없음 (pip install pyhwp 필요)', 'hwp5txt')
    try:
        proc = subprocess.run([str(exe), str(path)], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return ExtractResult(False, '', 'hwp5txt 실행 시간 초과 (30초)', 'hwp5txt')
    except OSError as e:
        return ExtractResult(False, '', f'hwp5txt 실행 실패: {e}', 'hwp5txt')
    if proc.returncode != 0:
        err = proc.stderr.decode('utf-8', errors='replace').strip()
        return ExtractResult(False, '', f'hwp5txt 변환 실패 (암호화되었거나 손상된 파일일 수 있음): {err[:200]}', 'hwp5txt')
    text = proc.stdout.decode('utf-8', errors='replace')
    if len(text) < MIN_CHARS:
        return _too_short('hwp5txt')
    return ExtractResult(True, text, None, 'hwp5txt')


def extract_pdf(path: Path) -> ExtractResult:
    try:
        import pypdf
    except ImportError:
        return ExtractResult(False, '', 'pypdf 가 설치되어 있지 않음 (pip install pypdf 필요)', 'pypdf')
    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt('')
            except Exception:
                return ExtractResult(False, '', 'PDF 가 암호로 보호되어 있어 읽을 수 없음', 'pypdf')
        text = '\n'.join((page.extract_text() or '') for page in reader.pages)
    except Exception as e:  # noqa: BLE001 — pypdf 가 다양한 예외를 던진다. 전부 "추출 실패"로 통일한다.
        return ExtractResult(False, '', f'PDF 를 읽을 수 없음: {e}', 'pypdf')
    if len(text) < MIN_CHARS:
        return _too_short('pypdf')  # 스캔 이미지 PDF 등 텍스트 레이어가 없는 경우도 여기로 온다 (OCR 은 범위 밖)
    return ExtractResult(True, text, None, 'pypdf')


EXTRACTORS = {
    '.md': extract_md_txt,
    '.txt': extract_md_txt,
    '.docx': extract_docx,
    '.hwpx': extract_hwpx,
    '.hwp': extract_hwp,
    '.pdf': extract_pdf,
}


def extract(path) -> ExtractResult:
    path = Path(path)
    if not path.is_file():
        return ExtractResult(False, '', '파일이 존재하지 않음', 'none')
    fn = EXTRACTORS.get(path.suffix.lower())
    if not fn:
        return ExtractResult(False, '', f'{path.suffix} 확장자는 지원하지 않음', 'unsupported')
    return fn(path)


def extract_all(paths):
    """[(path, ExtractResult), ...] 를 돌려준다."""
    return [(Path(p), extract(p)) for p in paths]


if __name__ == '__main__':
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')
    files = sorted(f for f in target.rglob('*') if f.is_file() and f.suffix.lower() in EXTRACTORS)
    ok = 0
    for f, r in extract_all(files):
        tag = 'OK  ' if r.ok else 'FAIL'
        detail = f'{len(r.text)}자 ({r.method})' if r.ok else r.error
        print(f'{tag}  {f.relative_to(target)}  —  {detail}')
        ok += r.ok
    print(f'\n총 {len(files)}개 중 성공 {ok}, 실패 {len(files) - ok}')
