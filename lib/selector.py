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
    '지원동기형': '가치관·동기',  # profile.md + 관심직무.md 가 주재료이고 경험은 보조이므로 약한 가중치만 준다
    '성장형': '성장·실패',
}

_META_FIELD = re.compile(r'^- (시기|기관명|역할|카테고리|키워드): (.+)$', re.M)
_TITLE = re.compile(r'^# (.+)$', re.M)


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


def pick(candidates: List[Candidate], max_count: int = 2) -> List[Candidate]:
    """Step 7 경험 선택 원칙: 최대 2개, 기본 1개. 두 번째는 점수가 있고(0점 제외) 이유가 있을
    때만, 그리고 첫 번째와 카테고리가 겹치거나 점수 차가 크지 않을 때만 포함한다
    ("두 경험이 자연스럽게 연결되어야 한다")."""
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
           max_count: int = 2, override_filenames: Optional[List[str]] = None):
    """전체 흐름. override_filenames 를 주면 알고리즘을 건너뛰고 그 파일들을 그대로 쓴다
    (S3-2 UI 에서 사용자가 직접 고른 경우). 그래도 순위 목록(candidates)은 항상 함께 돌려준다 —
    UI 가 "왜 다른 경험이 후보였는지"를 보여줄 수 있어야 하기 때문이다.

    반환: {'question_type', 'candidates': [Candidate, ...], 'selected': [Candidate, ...]}
    """
    experiences = load_all_experiences(exp_dir)
    q_type, candidates = rank_experiences(question, experiences, interest_text)

    if override_filenames:
        by_name = {c.experience.filename: c for c in candidates}
        selected = [by_name[f] for f in override_filenames if f in by_name]
    else:
        selected = pick(candidates, max_count)

    return {'question_type': q_type, 'candidates': candidates, 'selected': selected}
