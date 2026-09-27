// 아주 작은 마크다운 -> HTML 변환기 (고도화, 2026-09-27). 이 프로젝트가 실제로 만들어내는
// 문서(자소서/이력서/포트폴리오/트렌드 리서치)에 쓰이는 문법만 지원한다: #/##/### 제목,
// **굵게**, - 목록, 1. 번호 목록, | 표 |, > 인용, --- 구분선, [텍스트](URL) 링크, 문단.
//
// 외부 라이브러리(marked.js 등)를 CDN 으로 불러오지 않은 이유: 이 앱은 개인용 로컬 도구라
// 인터넷이 없어도 이미 저장된 문서는 읽을 수 있어야 한다고 판단했다. 그 대신 지원 범위를
// 이 프로젝트의 실제 출력 형식으로 좁혀서 구현 부담을 줄였다(lib/export.py 의 markdown_to_docx
// 와 같은 방식의 트레이드오프 — 범용 마크다운 전체를 지원하지 않는다).

function mdEscapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function mdInline(text) {
  let html = mdEscapeHtml(text);
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return html;
}

function renderMarkdown(text) {
  const lines = (text || '').split('\n');
  const out = [];
  let listTag = null;   // 'ul' | 'ol' | null — 지금 이어지고 있는 목록 종류
  let tableBuf = [];
  let paraBuf = [];      // 고도화(2026-09-27 버그 수정): 빈 줄 없이 이어지는 일반 텍스트 줄을
                          // 모았다가 한 문단으로 합친다. LLM 이 문장 중간에 줄바꿈을 넣는
                          // 경우(예: "...써야 구체적으로 보이며\n,\n핵심은...")가 실제로
                          // 많은데, 한 줄 = 한 <p> 로 즉시 렌더링하면 그 줄바꿈이 그대로
                          // 문단 구분으로 보여서 문장이 계단식으로 잘려 보였다. 표준
                          // 마크다운처럼 "빈 줄만 문단 구분"으로 바꾼다.

  function flushPara() {
    if (!paraBuf.length) return;
    // 줄바꿈을 공백 하나로 합치면 "...보이며\n,\n핵심은..." 같은 원문에서 쉼표 앞에
    // 어색한 공백이 남는다("보이며 , 핵심은"). 마침표/쉼표 등 앞의 공백만 없앤다.
    let joined = paraBuf.join(' ').replace(/ +([,.!?:;)\]%])/g, '$1');
    out.push(`<p>${joined}</p>`);
    paraBuf = [];
  }
  function flushList() {
    if (listTag) { out.push(`</${listTag}>`); listTag = null; }
  }
  function flushTable() {
    if (!tableBuf.length) return;
    // 구분선 행(|---|---|)은 걷어낸다
    const rows = tableBuf.filter(r => !/^\|?[\s:|-]+\|?$/.test(r.trim()));
    if (rows.length) {
      out.push('<table>');
      rows.forEach((row, idx) => {
        const cells = row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
        const tag = idx === 0 ? 'th' : 'td';
        out.push('<tr>' + cells.map(c => `<${tag}>${mdInline(c)}</${tag}>`).join('') + '</tr>');
      });
      out.push('</table>');
    }
    tableBuf = [];
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) { flushList(); flushTable(); flushPara(); continue; }

    const headerMatch = trimmed.match(/^(#{1,3})\s+(.*)$/);
    if (headerMatch) {
      flushList(); flushTable(); flushPara();
      const level = headerMatch[1].length;
      out.push(`<h${level}>${mdInline(headerMatch[2])}</h${level}>`);
      continue;
    }

    if (/^-{3,}$/.test(trimmed)) {
      flushList(); flushTable(); flushPara();
      out.push('<hr>');
      continue;
    }

    if (trimmed.startsWith('>')) {
      flushList(); flushTable(); flushPara();
      out.push(`<blockquote>${mdInline(trimmed.replace(/^>\s?/, ''))}</blockquote>`);
      continue;
    }

    if (/^\|.+\|$/.test(trimmed)) {
      flushList(); flushPara();
      tableBuf.push(line);
      continue;
    }

    const bulletMatch = trimmed.match(/^[-*]\s+(.*)$/);
    if (bulletMatch) {
      flushTable(); flushPara();
      if (listTag !== 'ul') { flushList(); out.push('<ul>'); listTag = 'ul'; }
      out.push(`<li>${mdInline(bulletMatch[1])}</li>`);
      continue;
    }

    const numberedMatch = trimmed.match(/^\d+\.\s+(.*)$/);
    if (numberedMatch) {
      flushTable(); flushPara();
      if (listTag !== 'ol') { flushList(); out.push('<ol>'); listTag = 'ol'; }
      out.push(`<li>${mdInline(numberedMatch[1])}</li>`);
      continue;
    }

    flushList(); flushTable();
    paraBuf.push(mdInline(trimmed));
  }
  flushList();
  flushTable();
  flushPara();
  return out.join('\n');
}
