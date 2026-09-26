"""문서 내보내기 — DOCX (ROADMAP S8-2). PDF 는 이번 단계에서 보류한다.

**PDF 를 보류한 이유(2026-09-26, 사용자 결정)**: 이 환경엔 LibreOffice 가 없어서 ROADMAP 이
1순위로 제안한 "DOCX 경유" 변환이 불가능하다. 대안인 WeasyPrint 는 pip 패키지 외에 시스템
라이브러리(pango)를 새로 설치해야 하는데, 이건 프로젝트 범위를 넘어 사용자 macOS 에 손을 대는
일이라 이번엔 하지 않기로 했다. lib/export.py 를 나중에 PDF 로 넓힐 때는 아래 마크다운 파싱
부분(_parse_blocks)을 그대로 재사용할 수 있다 — DOCX 전용 렌더링(_render_docx)만 갈아끼우면 된다.

outputs/ 의 문서(자소서.md, 이력서.md, portfolio-outline.md)는 전부 같은 마크다운 방언을
쓴다 — # 제목, ## 절, **굵게**, 문단, 수평선(---), 표, 인용(>), 목록(-). 문서 종류별로 다른
변환기를 만들지 않고 이 구조를 그대로 Word 스타일(Heading 1/2/3, 문단, 표)로 옮기는 일반
변환기 하나로 셋 다 처리한다 — 그래야 "문항 제목과 글자수 표기가 유지된다"는 완료 조건이
자소서.md 뿐 아니라 이력서/포트폴리오에도 똑같이 적용된다.
"""
import re
from pathlib import Path
from typing import List

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt

_HEADER = re.compile(r'^(#{1,3})\s+(.*)$')
_BOLD = re.compile(r'\*\*(.+?)\*\*')
_HR = re.compile(r'^-{3,}$')
_TABLE_ROW = re.compile(r'^\|(.+)\|$')
_TABLE_SEP = re.compile(r'^\|?[\s:|-]+\|?$')
_BULLET = re.compile(r'^[-*]\s+(.*)$')

# Word 의 기본 한글 글꼴 이름. 이 macOS 환경엔 실제로 설치되어 있지 않지만, 파일에는 이름만
# 적어두면 Word/한글이 열 때 자기 시스템에 있는 폰트로 매핑한다 — 그래도 "열어보면 한글이
# 안 깨져 보이는가"는 실제로 Word/한글에서 열어봐야 아는 항목이라(완료 조건 자체가 그렇게
# 쓰여 있다) 이 모듈의 테스트는 "동아시아 폰트 지정이 파일 안에 실제로 들어갔는가"까지만
# 코드로 검증한다.
KOREAN_FONT = '맑은 고딕'


def _set_korean_font(run, bold=False):
    run.font.name = KOREAN_FONT
    run.bold = bold
    # python-docx 의 run.font.name 은 로마자(w:rFonts/@w:ascii)만 바꾼다 — 한글처럼 동아시아
    # 문자에는 w:eastAsia 속성을 따로 안 주면 적용되지 않는다(실측: 이걸 빠뜨리면 한글 부분만
    # Word 기본값(바탕)으로 남는다). run._element 를 직접 건드려서 그 속성을 추가한다.
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is None:
        rfonts = rpr.makeelement(qn('w:rFonts'), {})
        rpr.append(rfonts)
    rfonts.set(qn('w:eastAsia'), KOREAN_FONT)


def _add_runs(paragraph, text: str, bold=False):
    """이 프로젝트의 outputs/ 문서가 실제로 쓰는 마크다운 강조는 **굵게** 뿐이다 — 그것만 지원한다."""
    pos = 0
    for m in _BOLD.finditer(text):
        if m.start() > pos:
            _set_korean_font(paragraph.add_run(text[pos:m.start()]), bold=bold)
        _set_korean_font(paragraph.add_run(m.group(1)), bold=True)
        pos = m.end()
    if pos < len(text) or pos == 0:
        _set_korean_font(paragraph.add_run(text[pos:]), bold=bold)


def _add_table(doc: Document, table_lines: List[str]):
    rows = [[c.strip() for c in row.strip().strip('|').split('|')]
            for row in table_lines if not _TABLE_SEP.match(row.strip())]
    if not rows:
        return
    ncols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=ncols)
    try:
        table.style = 'Light Grid Accent 1'
    except KeyError:
        pass  # 스타일이 없는 최소 템플릿이면 기본 표 형식으로 대체
    for r, row in enumerate(rows):
        for c in range(ncols):
            cell_text = row[c] if c < len(row) else ''
            _add_runs(table.rows[r].cells[c].paragraphs[0], cell_text)


def markdown_to_docx(markdown_text: str) -> Document:
    """outputs/ 방언의 마크다운을 Word Document 객체로 옮긴다. 파일로 저장하는 건 export_docx()."""
    doc = Document()
    lines = markdown_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        header_m = _HEADER.match(line)
        if header_m:
            level = len(header_m.group(1))
            p = doc.add_heading(level=min(level, 3))
            _add_runs(p, header_m.group(2).strip(), bold=True)
            i += 1
            continue

        if _HR.match(stripped):
            doc.add_paragraph('─' * 30)
            i += 1
            continue

        if stripped.startswith('>'):
            p = doc.add_paragraph()
            _add_runs(p, stripped.lstrip('>').strip())
            p.paragraph_format.left_indent = Pt(18)
            i += 1
            continue

        bullet_m = _BULLET.match(stripped)
        if bullet_m:
            try:
                p = doc.add_paragraph(style='List Bullet')
            except KeyError:
                p = doc.add_paragraph('• ')
            _add_runs(p, bullet_m.group(1))
            i += 1
            continue

        if _TABLE_ROW.match(stripped):
            table_lines = []
            while i < len(lines) and _TABLE_ROW.match(lines[i].strip()):
                table_lines.append(lines[i])
                i += 1
            _add_table(doc, table_lines)
            continue

        p = doc.add_paragraph()
        _add_runs(p, stripped)
        i += 1

    return doc


def export_docx(markdown_text: str, out_path: Path) -> Path:
    doc = markdown_to_docx(markdown_text)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path
