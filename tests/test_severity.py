"""검수 심각도 3단계와 범용 문장 검사 (S11-21).

공유받은 자소서 검수 프롬프트에서 가져왔다.
  - [반드시 수정] / [수정하면 더 좋아짐] / [굳이 수정할 필요 없음]
  - "회사명만 바꾸면 다른 회사에도 제출할 수 있는 내용은 없는가"

전자는 **고칠 것이 없을 때도 무언가 찾아내려는** 평가 도구의 경향을 코드가 거르기 위한
것이고, 후자는 그 판단의 일부를 코드가 결정적으로 할 수 있는 부분이다.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import coverletter as C  # noqa: E402


def review_json(issues):
    import json
    body = {'inferred_claim': '주장', 'paragraphs': [{'id': 'p1', 'inferred_claim': '주장'}],
            'sentences': [{'id': 's1', 'role': 'claim', 'supports': 'p1', 'fact_ids': []}],
            'issues': issues}
    return '===REVIEW===\n' + json.dumps(body, ensure_ascii=False) + '\n===END===\n'


class SeverityTest(unittest.TestCase):
    def parse(self, issues):
        data, err = C.parse_review(review_json(issues), ['s1'])
        self.assertIsNone(err, err)
        return data

    def test_must_fix_blocking_issue_triggers_repair(self):
        d = self.parse([{'sentence_ids': ['s1'], 'kind': 'unsupported_claim',
                         'severity': 'must_fix', 'reason': '근거 없음'}])
        self.assertEqual(len(C.blocking_issues(d)), 1)

    def test_optional_issue_does_not_trigger_repair(self):
        """차단성 유형이어도 평가자가 '굳이 고칠 필요 없음' 이라 했으면 수정하지 않는다.
        고치지 않아도 되는 것까지 고치면 사용자가 쓴 문장이 사라진다."""
        d = self.parse([{'sentence_ids': ['s1'], 'kind': 'unsupported_claim',
                         'severity': 'optional', 'reason': '취향 차이'}])
        self.assertEqual(C.blocking_issues(d), [])

    def test_better_issue_is_reported_but_not_blocking(self):
        d = self.parse([{'sentence_ids': ['s1'], 'kind': 'redundant',
                         'severity': 'better', 'reason': '중복'}])
        self.assertEqual(C.blocking_issues(d), [])
        self.assertEqual(C.issues_by_severity(d)['better'], 1)

    def test_missing_severity_falls_back_by_kind(self):
        """심각도를 안 적었다고 지적이 조용히 사라지면 안 된다."""
        d = self.parse([{'sentence_ids': ['s1'], 'kind': 'fabricated_fact', 'reason': 'x'},
                        {'sentence_ids': ['s1'], 'kind': 'redundant', 'reason': 'y'}])
        self.assertEqual(d['issues'][0]['severity'], C.SEVERITY_MUST_FIX)
        self.assertEqual(d['issues'][1]['severity'], C.SEVERITY_BETTER)
        self.assertEqual(len(C.blocking_issues(d)), 1)

    def test_unknown_severity_fails_the_review(self):
        _, err = C.parse_review(review_json(
            [{'sentence_ids': ['s1'], 'kind': 'redundant', 'severity': '매우심각'}]), ['s1'])
        self.assertIn('심각도', err)

    def test_counts_are_reported_per_severity(self):
        d = self.parse([{'sentence_ids': ['s1'], 'kind': 'fabricated_fact', 'severity': 'must_fix'},
                        {'sentence_ids': ['s1'], 'kind': 'redundant', 'severity': 'better'},
                        {'sentence_ids': ['s1'], 'kind': 'redundant', 'severity': 'optional'}])
        self.assertEqual(C.issues_by_severity(d),
                         {'must_fix': 1, 'better': 1, 'optional': 1})


class CompanySpecificityTest(unittest.TestCase):
    COMPANY = ('# 테스트기업\n\n'
               '- **Vision 2030**: "가장 신뢰받는 공급망 파트너"\n'
               '- 핵심가치: **Inspire through people**\n')

    def test_extracts_quoted_and_bold_phrases(self):
        terms = C.company_specific_terms(self.COMPANY)
        self.assertIn('가장 신뢰받는 공급망 파트너', terms)
        self.assertIn('Inspire through people', terms)

    def test_draft_using_the_vision_is_specific(self):
        got = C.check_company_specificity(
            '가장 신뢰받는 공급망 파트너가 되겠다는 방향에 공감합니다.', self.COMPANY)
        self.assertEqual(got, ['가장 신뢰받는 공급망 파트너'])

    def test_generic_draft_has_no_company_terms(self):
        """회사명만 바꾸면 어디에도 낼 수 있는 문장."""
        self.assertEqual(
            C.check_company_specificity('귀사의 비전에 깊이 공감하여 지원했습니다.', self.COMPANY),
            [])

    def test_whitespace_differences_are_tolerated(self):
        self.assertTrue(C.check_company_specificity(
            '가장  신뢰받는\n공급망 파트너', self.COMPANY))

    def test_thin_company_file_yields_no_terms_to_judge_against(self):
        """자료에 뽑을 고유 표현이 없으면 범용이라고 판정할 근거도 없다."""
        self.assertEqual(C.company_specific_terms('# 기업\n\n좋은 회사입니다.\n'), [])


if __name__ == '__main__':
    unittest.main()


class ClicheTest(unittest.TestCase):
    """S11-25. 자소서는 설득하는 글이다 — "열심히 했다" 는 일을 잘한다는 증거가 아니다."""

    def test_catches_filler_sentiment(self):
        self.assertEqual(C.check_cliche('열심히 노력하여 보람찼습니다.'), ['보람', '열심히'])

    def test_learning_is_not_flagged_in_a_cover_letter(self):
        """자소서에는 "배운 점" 을 묻는 문항이 실제로 있다. 이력서(master_resume)와 달리
        여기서 '배웠' 을 잡으면 오탐이 된다."""
        self.assertEqual(C.check_cliche('그 과정에서 배운 것은 순서였습니다.'), [])

    def test_concrete_writing_passes(self):
        self.assertEqual(
            C.check_cliche('수기 입력 단계를 없애 별도 정리 없이 확인할 수 있게 했습니다.'), [])

    def test_master_resume_lint_is_wider(self):
        from lib import master_resume as M
        self.assertTrue(M.VAGUE_REFLECTION.search('많이 배웠습니다'))
        self.assertEqual(C.check_cliche('많이 배웠습니다'), [])
