"""lib/selector.py 테스트 (S3-1). Step 5~7 을 LLM 없이 코드로 재현하는 부분이 핵심이다."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import selector as S  # noqa: E402

EXPERIENCE_TEMPLATE = """# {title}

## 메타

- 시기: {period}
- 기관명: {org}
- 역할: 참여자
- 카테고리: {category}
- 키워드: {keywords}

## 활동 내용

내용 요약.

## 상세 내용 (STAR)

### Situation (상황)
상황 설명입니다. 이 문장은 충분히 길게 써서 테스트용 더미 콘텐츠 역할을 합니다.

### Action (내가 한 것)
행동 설명입니다.

### Result (결과)
결과 설명입니다.

## STAR 품질 체크 (파싱 후 자동 평가)

- [x] Situation
- [x] Action
- [x] Result
{tags}
## 배운 것 / 느낀 것

배운 점.

## 이 경험으로 쓸 수 있는 문항 유형

- 예시
"""


def make_experience(dir_path, filename, title, category, keywords, tags='', period='2024'):
    content = EXPERIENCE_TEMPLATE.format(
        title=title, period=period, org='테스트기관', category=category, keywords=keywords, tags=tags)
    (dir_path / filename).write_text(content, encoding='utf-8')


class ClassifyQuestionTest(unittest.TestCase):
    def test_각_유형이_신호_문구로_분류된다(self):
        self.assertEqual(S.classify_question('가장 힘들었던 것을 극복한 경험을 서술하시오'), '경험형')
        self.assertEqual(S.classify_question('본인의 강점과 약점을 서술하시오'), '가치관형')
        self.assertEqual(S.classify_question('지원 동기를 서술하시오'), '지원동기형')
        self.assertEqual(S.classify_question('입사 후 포부를 서술하시오'), '지원동기형')
        self.assertEqual(S.classify_question('성장한 경험과 배운 점을 서술하시오'), '성장형')

    def test_신호가_없으면_경험형이_기본값(self):
        self.assertEqual(S.classify_question('아무 신호도 없는 임의의 문장입니다'), '경험형')


class LoadExperienceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_메타_필드를_파싱한다(self):
        make_experience(self.tmp, 'a.md', '테스트 활동', '리더십 / 팀워크', '갈등조율, 소통')
        exp = S.load_experience(self.tmp / 'a.md')
        self.assertEqual(exp.title, '테스트 활동')
        self.assertEqual(exp.category, ['리더십', '팀워크'])
        self.assertEqual(exp.keywords, ['갈등조율', '소통'])
        self.assertFalse(exp.old_flag)
        self.assertFalse(exp.star_weak)

    def test_경고_태그를_인식한다(self):
        make_experience(self.tmp, 'b.md', '오래된 활동', '리더십', '리더십',
                         tags='\n⚠️ 오래된 경험\n⚠️ STAR 보강 필요: Result 부족\n')
        exp = S.load_experience(self.tmp / 'b.md')
        self.assertTrue(exp.old_flag)
        self.assertTrue(exp.star_weak)


class EmphasisMatchTest(unittest.TestCase):
    """실측 버그 회귀: 짧은 일반 단어(예: "학술용역")만 겹쳐도 매칭되면 안 된다."""

    def test_짧은_일반_단어_하나만_겹치면_매칭_아님(self):
        interest = "## 물류/SCM\n### 이 직무에서 강조할 경험 유형\n\n- 한국산업안전보건공단 학술용역 (데이터 기반 설문)\n"
        self.assertFalse(S._emphasized_in_interest('전세사기 보드게임개발 학술용역', interest))

    def test_같은_경험이_이름만_다르게_적혀도_충분히_겹치면_매칭(self):
        interest = "### 이 직무에서 강조할 경험 유형\n\n- 교수학생전공심화연구모임 (연구·분석 역량)\n"
        self.assertTrue(S._emphasized_in_interest('교수-학생 전공심화 연구모임 / 학부연구생', interest))

    def test_전혀_무관하면_매칭_아님(self):
        interest = "### 이 직무에서 강조할 경험 유형\n\n- 현대글로비스 호주 인턴십 (선적서류)\n"
        self.assertFalse(S._emphasized_in_interest('가로수뜨개질 봉사', interest))


class SynonymMatchTest(unittest.TestCase):
    """고도화(2026-09-26): S3-1 알려진 한계였던 "해외 문항이 글로벌/호주 키워드를 단 경험과
    연결 안 됨"을 실제로 고쳤는지 확인한다."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _exp(self, filename, title, keywords, category='전문성·직무역량'):
        make_experience(self.tmp, filename, title, category, keywords)
        return S.load_experience(self.tmp / filename)

    def test_해외_문항이_글로벌_키워드_경험과_연결된다(self):
        """S3-1 실측에서 실패했던 바로 그 사례."""
        exp = self._exp('a.md', '글로벌 사회공헌단 활동', '글로벌, 봉사')
        matched = S._matched_synonyms('해외 경험에 대해 서술하시오', exp)
        self.assertTrue(any('해외' in m and '글로벌' in m for m in matched))

    def test_해외_문항이_호주_제목_경험과_연결된다(self):
        exp = self._exp('a.md', '호주 인턴십', '인턴')
        matched = S._matched_synonyms('해외 경험에 대해 서술하시오', exp)
        self.assertTrue(any('해외' in m for m in matched))

    def test_동의어_매칭이_점수와_사유에_반영된다(self):
        make_experience(self.tmp, 'a.md', '글로벌 인턴십', '전문성·직무역량', '글로벌')
        make_experience(self.tmp, 'b.md', '무관한 경험', '리더십', '팀워크')
        experiences = S.load_all_experiences(self.tmp)
        _, candidates = S.rank_experiences('해외 경험을 서술하시오', experiences)
        by_name = {c.experience.filename: c for c in candidates}
        self.assertGreater(by_name['a.md'].score, by_name['b.md'].score)
        self.assertTrue(any('동의어' in r for r in by_name['a.md'].reasons))

    def test_완전히_같은_단어는_이중_가산되지_않는다(self):
        """"해외" 문항에 "해외"라는 키워드가 그대로 있으면 _matched_keywords 가 이미 잡으므로
        동의어 매칭에서는 세지 않는다(같은 신호를 두 번 점수화하지 않기 위해)."""
        exp = self._exp('a.md', '해외 봉사활동', '해외, 봉사')
        matched = S._matched_synonyms('해외 경험을 서술하시오', exp)
        self.assertEqual(matched, [])

    def test_무관한_경험은_동의어로도_연결되지_않는다(self):
        exp = self._exp('a.md', '교내 스터디', '학습')
        matched = S._matched_synonyms('해외 경험을 서술하시오', exp)
        self.assertEqual(matched, [])


class RankAndPickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        make_experience(self.tmp, '2022-a.md', '리더십 경험 A', '리더십', '리더십, 팀워크', period='2022')
        make_experience(self.tmp, '2024-b.md', '리더십 경험 B', '리더십', '리더십, 갈등조율', period='2024')
        make_experience(self.tmp, '2023-c.md', '데이터 분석 경험', '전문성·직무역량', '데이터분석, 파이썬', period='2023')
        self.experiences = S.load_all_experiences(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_키워드가_겹치는_경험이_1순위(self):
        q_type, candidates = S.rank_experiences('리더십을 발휘한 경험을 서술하시오', self.experiences)
        self.assertEqual(q_type, '경험형')
        self.assertIn(candidates[0].experience.title, ('리더십 경험 A', '리더십 경험 B'))
        self.assertGreater(candidates[0].score, 0)

    def test_동점이면_최근_경험이_우선(self):
        q_type, candidates = S.rank_experiences('리더십을 발휘한 경험을 서술하시오', self.experiences)
        top_two = [c.experience.filename for c in candidates[:2]]
        self.assertEqual(top_two, ['2024-b.md', '2022-a.md'])  # 둘 다 "리더십" 키워드 겹침, 최근이 우선

    def test_무관한_경험은_점수가_낮다(self):
        q_type, candidates = S.rank_experiences('리더십을 발휘한 경험을 서술하시오', self.experiences)
        by_name = {c.experience.filename: c.score for c in candidates}
        self.assertGreater(by_name['2024-b.md'], by_name['2023-c.md'])

    def test_pick_기본은_1개가_아니라_관련성_있으면_2개까지(self):
        _, candidates = S.rank_experiences('리더십을 발휘한 경험을 서술하시오', self.experiences)
        picked = S.pick(candidates, max_count=2)
        self.assertLessEqual(len(picked), 2)
        self.assertGreaterEqual(len(picked), 1)

    def test_pick_max_count_1이면_하나만(self):
        _, candidates = S.rank_experiences('리더십을 발휘한 경험을 서술하시오', self.experiences)
        picked = S.pick(candidates, max_count=1)
        self.assertEqual(len(picked), 1)

    def test_무관한_2위는_2개로_묶이지_않는다(self):
        """1위와 카테고리도 다르고 점수 차도 크면 "자연스럽게 연결"되지 않으므로 혼자 나가야 한다."""
        q_type, candidates = S.rank_experiences('데이터 분석 경험을 서술하시오', self.experiences)
        picked = S.pick(candidates, max_count=2)
        # 데이터 분석 경험(키워드 2개 겹침, 점수 높음) 뒤로, 무관한 리더십 경험들은 점수 0이라 제외되어야 함
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].experience.title, '데이터 분석 경험')


class SelectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        make_experience(self.tmp, '2022-a.md', '경험 A', '리더십', '리더십', period='2022')
        make_experience(self.tmp, '2024-b.md', '경험 B', '전문성·직무역량', '데이터분석', period='2024')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_override_없으면_알고리즘이_고른다(self):
        r = S.select(self.tmp, '리더십을 발휘한 경험을 서술하시오')
        self.assertTrue(any(c.experience.filename == '2022-a.md' for c in r['selected']))

    def test_override_주면_그대로_쓴다_S3_1_완료조건(self):
        """완료 조건: 같은 문항에 경험 A, B 로 각각 생성 가능."""
        same_question = '리더십을 발휘한 경험을 서술하시오'
        r_a = S.select(self.tmp, same_question, override_filenames=['2022-a.md'])
        r_b = S.select(self.tmp, same_question, override_filenames=['2024-b.md'])
        self.assertEqual([c.experience.filename for c in r_a['selected']], ['2022-a.md'])
        self.assertEqual([c.experience.filename for c in r_b['selected']], ['2024-b.md'])
        # candidates(전체 후보 목록)는 override 여부와 무관하게 항상 제공되어야 한다 (UI 가 보여줄 수 있게)
        self.assertEqual(len(r_a['candidates']), 2)
        self.assertEqual(len(r_b['candidates']), 2)

    def test_override에_없는_파일명은_무시된다(self):
        r = S.select(self.tmp, '아무 질문', override_filenames=['2022-a.md', '없는파일.md'])
        self.assertEqual([c.experience.filename for c in r['selected']], ['2022-a.md'])

    def test_override_미지_파일명은_보고된다(self):
        """S11-2: selected 에서 빼는 동작은 그대로 두고(위 테스트), 조용히 버리지 않고
        unknown_overrides 로 보고한다. 중단 여부는 호출자가 정한다."""
        r = S.select(self.tmp, '아무 질문', override_filenames=['2022-a.md', '없는파일.md'])
        self.assertEqual(r['unknown_overrides'], ['없는파일.md'])
        self.assertEqual(r['dropped_overrides'], [])

    def test_override도_상한을_지킨다(self):
        """S11-2: 예전엔 override 경로가 max_count 를 통째로 무시해서 몇 개든 들어갔다."""
        r = S.select(self.tmp, '아무 질문',
                     max_count=1, override_filenames=['2022-a.md', '2024-b.md'])
        self.assertEqual([c.experience.filename for c in r['selected']], ['2022-a.md'])
        self.assertEqual(r['dropped_overrides'], ['2024-b.md'])

    def test_override_중복은_제거하고_보고한다(self):
        """S11-11: 같은 파일을 두 번 지정하면 상한 2개를 한 경험으로 채워버린다."""
        r = S.select(self.tmp, '아무 질문', override_filenames=['2022-a.md', '2022-a.md'])
        self.assertEqual([c.experience.filename for c in r['selected']], ['2022-a.md'])
        self.assertEqual(r['duplicate_overrides'], ['2022-a.md'])

    def test_기본_상한은_Claude_md_의_2개(self):
        self.assertEqual(S.MAX_EXPERIENCES, 2)

    def test_경험이_하나도_없으면_빈_목록(self):
        empty_dir = self.tmp / 'empty'
        empty_dir.mkdir()
        r = S.select(empty_dir, '아무 질문')
        self.assertEqual(r['selected'], [])
        self.assertEqual(r['candidates'], [])


if __name__ == '__main__':
    unittest.main()
