"""경험 선택 로직 (ROADMAP S3-1). LLM 을 쓰지 않고 코드로 고른다.

write-coverletter.md 의 Step 5(문항 분석)~Step 7(경험 선택)을 그대로 코드로 옮긴 것이다.
원래는 이 세 단계가 Step 8(문단 작성)과 함께 LLM 호출 한 번에 묶여 있었다. 그러면:
  - 사용자가 경험을 직접 고를 수 없다 (모델이 고른 걸 받아들이는 수밖에 없다)
  - 왜 그 경험이 뽑혔는지 보여줄 수 없다 (모델의 "참조한 경험" 한 줄이 전부다)
  - 경험을 바꿔 재생성하려면 프롬프트를 통째로 다시 손봐야 한다
  - 같은 입력이어도 모델이 매번 다른 경험을 고를 수 있어 결과가 재현되지 않는다
  - 사용자의 모든 경험 파일 전문을 매번 프롬프트에 실어야 해서 비용이 크다 (S1-3 의 알려진 한계)

이 모듈은 선택만 담당한다. 글쓰기(Step 8)는 여전히 LLM 이 하지만, 이 모듈이 고른 1~2개
경험의 전문만 받는다 — 후보 전체를 다시 보지 않는다.
"""
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# write-coverletter.md Step 6 의 표를 그대로 옮겼다. 실제 질문 문구는 표의 예시보다 다양하므로
# 같은 취지의 흔한 표현을 보수적으로 추가했다 (예: 표에 "지원 동기"라는 말 자체가 없어서 추가함).
QUESTION_TYPE_TRIGGERS = {
    '경험형': ['경험을 말', '경험을 서술', '경험을 기술', '사례를 들어', '사례를 서술', '극복한 경험', '발휘한 경험', '수행한 경험'],
    '가치관형': ['본인의 가치관', '가치관을', '강점', '약점', '어떤 사람인지', '성격의 장단점', '본인을 표현'],
    '지원동기형': ['지원한 이유', '지원 동기', '왜 이 직무', '왜 저희', '왜 우리', '입사 후 목표', '입사 후 포부', '포부를'],
    '성장형': ['성장한 경험', '배운 점', '배운 것', '실패에서 얻은', '실패를 통해', '변화한 것', '성장 과정'],
}

# Step 6 "유형별 사용 재료" 를 카테고리 가중치로 옮겼다. index.md/experience-entry.md 의 카테고리 값과 맞춘다.
TYPE_PREFERRED_CATEGORY = {
    '경험형': None,          # 전문성·직무역량/문제해결/리더십/팀워크 등 전체가 대상, 특정 카테고리 가중치 없음
    '가치관형': '가치관·동기',
    '지원동기형': None,      # 스킬 원문: 보조 재료는 "직무 관련 경험" — 가치관·동기 카테고리가 아니다.
                             # 직무 관련성은 카테고리가 아니라 _emphasized_in_interest() 의 3순위
                             # (관심직무.md 강조 매칭) 가 이미 담당한다. 여기서 또 카테고리로 가중치를
                             # 주면 "봉사 경험"처럼 가치관·동기 카테고리인 무관한 경험이 잘못 앞서 나간다
                             # (실측으로 확인: 데이터분석가 지원동기 질문에 해외봉사 경험이 앞섰음)
    '성장형': '성장·실패',
}

# Claude.md "경험 사용 원칙" 이 이 값의 정식 출처다 (최대 2개, 기본 1개).
# 여기 숫자를 바꾸기 전에 그 절을 먼저 고친다.
MAX_EXPERIENCES = 2

_META_FIELD = re.compile(r'^- (시기|기관명|역할|카테고리|키워드): (.+)$', re.M)
_TITLE = re.compile(r'^# (.+)$', re.M)

# 고도화(2026-09-26, 사용자 요청): "순수 문자열 매칭이라 동의어를 모른다"는 S3-1 의 알려진
# 한계(실측: "해외" 문항이 "글로벌"/"호주" 키워드를 단 경험과 연결되지 않음)를 줄인다.
# LLM 을 쓰면 바로 해결되지만 그러면 이 모듈 전체의 존재 이유(재현 가능성, 비용 0, 근거 투명성)
# 가 없어진다 — 그래서 여기서도 동의어를 "판단"이 아니라 고정된 사전으로 코드화한다. 완전한
# 동의어 커버리지는 원래 불가능하고(사전에 없는 표현은 여전히 놓친다), 목록은 이 프로젝트의
# 실제 경험 카테고리·실측에서 반복 확인된 것들로 우선 채웠다 — 새 사각지대가 발견되면 그룹만
# 추가하면 된다(코드 구조는 안 바뀜).
SYNONYM_GROUPS = [
    # 국가명은 "해외"의 동의어가 아니라 사례(instance-of)지만, 실제 경험 파일 제목에 국가명이
    # 그대로 들어가는 경우가 흔해서(예: "호주 인턴십", "교환학생-네덜란드") 실용적으로 여기 묶어둔다.
    # 전 세계 국가를 다 넣을 수는 없다 — 유학/인턴 목적지로 흔한 것 위주로만 채운다(완전한 목록이
    # 아니라는 걸 알면서 하는 트레이드오프. 안 걸리는 국가는 여전히 못 잡는다).
    {'해외', '글로벌', '국외', '외국', '교환학생', '유학', '어학연수',
     '호주', '미국', '영국', '중국', '일본', '독일', '프랑스', '네덜란드', '캐나다', '싱가포르'},
    {'리더십', '팀장', '리더', '주도', '총괄', '회장'},
    {'데이터분석', '데이터', '분석', 'AI', '인공지능', '머신러닝', '데이터사이언스'},
    {'협업', '팀워크', '소통', '커뮤니케이션', '조율', '갈등조율'},
    {'봉사', '사회공헌', '기부', '나눔'},
    {'창업', '스타트업', '사업기획', '기획'},
    {'인턴', '인턴십', '실무경험', '현장경험'},
    {'공모전', '경진대회', '해커톤', '챌린지', '수상'},
]


@dataclass
class Experience:
    filename: str
    title: str
    category: List[str]
    keywords: List[str]
    old_flag: bool
    star_weak: bool
    path: Path


@dataclass
class Candidate:
    experience: Experience
    score: float
    reasons: List[str] = field(default_factory=list)


def classify_question(question: str) -> str:
    """Step 6: 질문을 4가지 유형 중 하나로 분류한다. 신호가 여럿이면 가장 많이 겹친 유형,
    없으면 '경험형' (가장 무난한 기본값 — STAR 구조를 그대로 쓸 수 있다)."""
    scores = {t: sum(1 for trigger in triggers if trigger in question)
              for t, triggers in QUESTION_TYPE_TRIGGERS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else '경험형'


def load_experience(path: Path) -> Experience:
    text = path.read_text(encoding='utf-8')
    title_m = _TITLE.search(text)
    title = title_m.group(1).strip() if title_m else path.stem

    fields = {k: v.strip() for k, v in _META_FIELD.findall(text)}
    category = [c.strip() for c in fields.get('카테고리', '').split('/') if c.strip()]
    keywords = [k.strip() for k in fields.get('키워드', '').split(',') if k.strip()]

    return Experience(
        filename=path.name,
        title=title,
        category=category,
        keywords=keywords,
        old_flag='⚠️ 오래된 경험' in text,
        star_weak='⚠️ STAR 보강' in text,
        path=path,
    )


def load_all_experiences(exp_dir: Path) -> List[Experience]:
    if not exp_dir.exists():
        return []
    return [load_experience(p) for p in sorted(exp_dir.glob('*.md'))]


def _matched_keywords(question: str, keywords: List[str]) -> List[str]:
    """Step 7 매핑 기준 1순위: 문항 키워드와 경험 키워드가 직접 겹치는 것.
    별도 형태소 분석 없이, 그 경험의 키워드가 문항 문자열 안에 그대로 등장하는지만 본다
    (재현 가능해야 하므로 규칙을 단순하게 유지한다)."""
    return [kw for kw in keywords if kw and kw in question]


def _matched_title_words(question: str, title: str) -> List[str]:
    """키워드 목록에는 없어도 제목에 등장하는 고유명사(기관명 등)가 문항에 그대로 나오면 그것도 신호다.
    키워드 매칭보다는 약한 가중치를 준다 (아래 rank_experiences 참고)."""
    words = [w for w in re.split(r'[\s()·,/]+', title) if len(w) >= 2]
    return [w for w in words if w in question]


def _matched_synonyms(question: str, exp: 'Experience') -> List[str]:
    """문항에 등장한 표현과, 경험의 키워드/제목에 등장한 (다른) 표현이 같은 동의어 그룹에
    속하면 그 짝을 "질문표현↔경험표현" 형태로 돌려준다. 완전히 같은 단어는 이미
    _matched_keywords()/_matched_title_words() 가 잡으므로 여기서는 세지 않는다(이중 가산 방지)."""
    haystack = ' '.join(exp.keywords) + ' ' + exp.title
    found = []
    for group in SYNONYM_GROUPS:
        q_hit = next((t for t in group if t in question), None)
        if not q_hit:
            continue
        h_hit = next((t for t in group if t != q_hit and t in haystack), None)
        if h_hit:
            found.append(f'{q_hit}↔{h_hit}')
    return found


def _recency_key(filename: str):
    """동점일 때 타이브레이커로 쓴다. 파일명 맨 앞 연도가 최근일수록 우선한다
    (0000- 은 시기 불명 취급으로 가장 낮게 둔다). 완전한 대체재는 아니지만, 점수가 같을 때
    매번 같은 두 경험만 뽑히는 것보다는 최근 경험을 우선하는 편이 실전에서 더 쓸모 있다."""
    m = re.match(r'(\d{4})-', filename)
    year = int(m.group(1)) if m else 0
    return year if year > 0 else -1


_EMPHASIS_MIN_OVERLAP = 6  # 이보다 짧게 겹치면 "학술용역", "연구" 같은 일반 단어로 오탐할 수 있다 (실측으로 확인)


def _emphasized_in_interest(title: str, interest_text: str) -> bool:
    """Step 7 매핑 기준 3순위: 관심직무.md 의 "이 직무에서 강조할 경험 유형" 에 이 경험 제목이
    언급되어 있는가.

    단어 하나만 겹쳐도 매칭으로 치면 "학술용역", "활동", "연구" 같은 일반적인 단어 때문에
    무관한 경험까지 전부 걸린다 (실측: "전세사기 보드게임개발 학술용역" 이 "한국산업안전보건공단
    학술용역" 과 무관한데도 매칭됨). 공백·기호를 없앤 뒤 6자 이상 연속으로 겹칠 때만 인정한다.
    """
    if not interest_text or not title:
        return False
    blocks = re.findall(r'강조할 경험 유형\s*\n(.*?)(?=\n##|\n---|\Z)', interest_text, re.S)
    combined = '\n'.join(blocks) if blocks else interest_text
    norm_title = re.sub(r'[\s\-·/()]+', '', title)

    for line in combined.splitlines():
        ident = line.split('(')[0].lstrip('- ').strip()  # "- 현대글로비스 호주 인턴십 (선적서류...)" → 괄호 앞부분만
        norm_ident = re.sub(r'[\s\-·/()]+', '', ident)
        if not norm_ident:
            continue
        match = difflib.SequenceMatcher(None, norm_title, norm_ident).find_longest_match(
            0, len(norm_title), 0, len(norm_ident))
        if match.size >= _EMPHASIS_MIN_OVERLAP:
            return True
    return False


def rank_experiences(question: str, experiences: List[Experience],
                      interest_text: str = '') -> (str, List[Candidate]):
    """Step 5~7. (질문 유형, 점수순 후보 목록) 을 돌려준다. 점수 0 인 것도 포함한다 —
    후보가 하나도 없는 것보다는, 근거가 약해도 뭐라도 고르는 게 낫다 (경험형은 원래 카테고리
    가중치가 없어서 대부분 키워드 매칭에만 의존한다)."""
    q_type = classify_question(question)
    preferred_category = TYPE_PREFERRED_CATEGORY[q_type]

    candidates = []
    for exp in experiences:
        score, reasons = 0.0, []

        matched = _matched_keywords(question, exp.keywords)
        if matched:
            score += 2 * len(matched)
            reasons.append(f'문항과 겹치는 키워드: {", ".join(matched)}')

        title_matched = _matched_title_words(question, exp.title)
        if title_matched:
            score += 1 * len(title_matched)
            reasons.append(f'문항과 겹치는 제목 단어: {", ".join(title_matched)}')

        synonym_matched = _matched_synonyms(question, exp)
        if synonym_matched:
            score += 1.5 * len(synonym_matched)  # 정확히 같은 단어(2점)보다는 약하고, 제목 단어(1점)보다는 강한 신호
            reasons.append(f'동의어로 연결되는 표현: {", ".join(synonym_matched)}')

        if preferred_category and preferred_category in exp.category:
            score += 1.5
            reasons.append(f'{q_type}에 맞는 카테고리({preferred_category})')

        if _emphasized_in_interest(exp.title, interest_text):
            score += 1
            reasons.append('관심직무.md 가 이 직무에서 강조하라고 지정한 경험')

        if exp.star_weak:
            score -= 0.3  # 완전히 배제하진 않되, 근거가 부실한 경험은 약간 감점한다
        if exp.old_flag:
            reasons.append('⚠️ 오래된 경험 — 사용 전 확인 필요')

        candidates.append(Candidate(experience=exp, score=round(score, 2), reasons=reasons))

    # 점수가 같으면 최근 경험을 우선한다 (동점일 때 매번 같은 두 경험만 뽑히는 것을 줄인다)
    candidates.sort(key=lambda c: (c.score, _recency_key(c.experience.filename)), reverse=True)
    return q_type, candidates


def pick(candidates: List[Candidate], max_count: int = MAX_EXPERIENCES) -> List[Candidate]:
    """Step 7 경험 선택 원칙: 최대 2개, 기본 1개. 두 번째는 점수가 있고(0점 제외) 이유가 있을
    때만, 그리고 첫 번째와 카테고리가 겹치거나 점수 차가 크지 않을 때만 포함한다
    ("두 경험이 자연스럽게 연결되어야 한다").

    **한계(S11-2에서 명시)**: 아래 조건은 *검색 휴리스틱*이지, 두 경험이 하나의 논지를
    함께 받친다는 *검증*이 아니다. 카테고리가 겹치거나 점수가 비슷하다는 것은
    "둘 다 분석 경험이다" 수준의 근거일 뿐이고, 두 번째 경험을 빼면 논증이 실제로
    약해지는지는 이 함수가 알지 못한다. 그 판정은 S11-10(논지 계획 + 검토 호출)이 맡는다.
    여기서는 후보를 좁히기만 하고, 근거는 Candidate.reasons 로 그대로 드러낸다.
    """
    if not candidates:
        return []
    top = [candidates[0]]
    if max_count >= 2 and len(candidates) > 1:
        second = candidates[1]
        same_category = bool(set(second.experience.category) & set(candidates[0].experience.category))
        close_score = candidates[0].score > 0 and second.score >= candidates[0].score * 0.4
        if second.score > 0 and (same_category or close_score):
            top.append(second)
    return top


def select(exp_dir: Path, question: str, interest_text: str = '',
           max_count: int = MAX_EXPERIENCES, override_filenames: Optional[List[str]] = None):
    """전체 흐름. override_filenames 를 주면 알고리즘을 건너뛰고 그 파일들을 그대로 쓴다
    (S3-2 UI 에서 사용자가 직접 고른 경우). 그래도 순위 목록(candidates)은 항상 함께 돌려준다 —
    UI 가 "왜 다른 경험이 후보였는지"를 보여줄 수 있어야 하기 때문이다.

    **S11-2**: 예전에는 override 경로가 max_count 를 통째로 무시했고, 모르는 파일명을
    조용히 버렸다. UI 가 2개로 제한해도 API 를 직접 부르면 몇 개든 들어갔고, 파일명에
    오타가 있으면 아무 말 없이 빠진 채 생성됐다(전부 틀리면 경험 0개로 글을 썼다).

    이 함수는 **판정하지 않고 보고한다.** 중단할지 경고만 할지는 호출자가 정한다 —
    웹은 에러 이벤트, 대화형은 되묻기처럼 경로마다 처리가 다르기 때문이다.

    반환: {'question_type', 'candidates', 'selected',
           'unknown_overrides': 존재하지 않는 파일명,
           'dropped_overrides': 상한을 넘어 잘린 파일명,
           'duplicate_overrides': 중복 지정된 파일명}
    """
    experiences = load_all_experiences(exp_dir)
    q_type, candidates = rank_experiences(question, experiences, interest_text)

    unknown_overrides: List[str] = []
    dropped_overrides: List[str] = []
    duplicate_overrides: List[str] = []

    if override_filenames:
        by_name = {c.experience.filename: c for c in candidates}
        unknown_overrides = [f for f in override_filenames if f not in by_name]
        # S11-11: 같은 파일을 두 번 지정하면 상한 2개를 "한 경험으로" 채워버린다.
        # 조용히 중복 제거하되 보고한다 — 사용자가 다른 경험을 고르려던 것일 수 있다.
        seen = set()
        deduped = []
        for f in override_filenames:
            if f in seen:
                if f not in duplicate_overrides:
                    duplicate_overrides.append(f)
                continue
            seen.add(f)
            deduped.append(f)
        chosen = [by_name[f] for f in deduped if f in by_name]
        dropped_overrides = [c.experience.filename for c in chosen[max_count:]]
        selected = chosen[:max_count]
    else:
        selected = pick(candidates, max_count)

    return {'question_type': q_type, 'candidates': candidates, 'selected': selected,
            'unknown_overrides': unknown_overrides, 'dropped_overrides': dropped_overrides,
            'duplicate_overrides': duplicate_overrides}
