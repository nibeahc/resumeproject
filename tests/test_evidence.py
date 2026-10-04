"""근거 구간 검증 (S11-19, 2차 설계검토 3-2).

검토의 요구는 5단계였다.
  1. 모델이 사실 주장을 분리한다
  2. 모델이 근거 구간을 제시한다
  3. **코드가 그 인용문이 원본에 실제로 있는지 검사한다**   ← 이 파일이 지키는 것
  4. 별도 평가가 주장과 근거의 관계를 판단한다              ← skills/review-coverletter.md
  5. 불충분한 주장을 사용자 확인 대상으로 보낸다

3번이 핵심이다. "이 주장은 저 파일의 이 구절이 근거다" 는 모델의 자기보고지만,
그 구절이 그 파일에 있는지는 코드가 확인할 수 있다.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import coverletter as C  # noqa: E402
from test_coverletter import EngineFixture, ScriptedLLM, bounded, llm_result, review  # noqa: E402

INTERN = ('ETA를 트래킹하고 일일 보고서를 작성하여 팀 내 공유하였다. '
          'EV배터리 회수 운송 폐기와 관련된 특수 물류 절차를 처리하였다.')


def block(*rows):
    return '===EVIDENCE===\n' + '\n'.join(rows) + '\n===END===\n'


class ParseEvidenceTest(unittest.TestCase):
    def test_parses_rows_and_strips_sentence_prefix(self):
        rows = C.parse_evidence(block('c1 | s3 | 보고서를 작성했다 | action | 2026-인턴.md | 일일 보고서를 작성하여'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['sentence'], 3)
        self.assertEqual(rows[0]['file'], '2026-인턴.md')
        self.assertEqual(rows[0]['claim_type'], 'action')

    def test_no_block_is_not_an_error(self):
        self.assertEqual(C.parse_evidence('===ANSWER===\n가\n===END===\n'), [])

    def test_header_and_example_lines_are_skipped(self):
        rows = C.parse_evidence(block(
            '주장id | 문장번호 | 주장 | 유형 | 파일 | 인용',
            '예시: c1 | s1 | 예시 | action | x.md | y',
            'c1 | s1 | 진짜 | action | 2026-인턴.md | 일일 보고서'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['claim'], '진짜')


class QuoteVerificationTest(unittest.TestCase):
    """2026-10-04 에 실제로 잡힌 거짓 주장을 그대로 시험 사례로 쓴다."""

    SRC = {'2026-인턴.md': INTERN}
    ANSWER = '일일 보고서를 작성했습니다. 그 보고서가 담당자들의 업무 순서 기준이 되었습니다.'

    def test_real_quote_passes(self):
        rows = C.parse_evidence(block(
            'c1 | s1 | 보고서를 작성했다 | action | 2026-인턴.md | 일일 보고서를 작성하여 팀 내 공유하였다',
            'c2 | s2 | 업무 순서의 기준이 되었다 | impact | 2026-인턴.md | 일일 보고서를 작성하여'))
        self.assertEqual(C.lint_evidence(rows, self.ANSWER, self.SRC), [])

    def test_invented_quote_is_caught(self):
        """"업무 순서의 기준이 되었다" 는 원문에 없다. 이것이 검토가 말한 insufficient 다."""
        rows = C.parse_evidence(block(
            'c1 | s1 | 보고서를 작성했다 | action | 2026-인턴.md | 일일 보고서를 작성하여',
            'c2 | s2 | 업무 순서의 기준 | impact | 2026-인턴.md | 담당자들이 이것으로 순서를 정했다'))
        self.assertIn(C.ISSUE_EVIDENCE_QUOTE_NOT_FOUND,
                      C.lint_evidence(rows, self.ANSWER, self.SRC))
        report = C.evidence_report(rows, self.SRC)
        self.assertEqual([r['verdict'] for r in report], ['quote_found', 'quote_not_found'])

    def test_whitespace_differences_are_tolerated(self):
        rows = C.parse_evidence(block(
            'c1 | s1 | 작성 | action | 2026-인턴.md | 일일  보고서를\t작성하여',
            'c2 | s2 | 작성 | action | 2026-인턴.md | 일일 보고서를 작성하여'))
        self.assertEqual(C.lint_evidence(rows, self.ANSWER, self.SRC), [])

    def test_unknown_file_is_caught(self):
        rows = C.parse_evidence(block('c1 | s1 | 주장 | action | 없는파일.md | 일일 보고서를 작성하여'))
        issues = C.lint_evidence(rows, self.ANSWER, self.SRC)
        self.assertIn(C.ISSUE_EVIDENCE_UNKNOWN_FILE, issues)

    def test_sentence_number_out_of_range_is_caught(self):
        rows = C.parse_evidence(block('c1 | s9 | 주장 | action | 2026-인턴.md | 일일 보고서를 작성하여'))
        self.assertIn(C.ISSUE_EVIDENCE_BAD_SENTENCE,
                      C.lint_evidence(rows, self.ANSWER, self.SRC))

    def test_uncovered_self_claim_is_caught(self):
        rows = C.parse_evidence(block(
            'c1 | s1 | 보고서를 작성했다 | action | 2026-인턴.md | 일일 보고서를 작성하여'))
        self.assertIn(C.ISSUE_EVIDENCE_UNCOVERED,
                      C.lint_evidence(rows, self.ANSWER, self.SRC))

    def test_missing_block_with_factual_claims(self):
        self.assertEqual(C.lint_evidence([], self.ANSWER, self.SRC),
                         [C.ISSUE_EVIDENCE_MISSING])

    def test_no_factual_claims_needs_no_evidence(self):
        """포부와 계획만 있는 글에 '근거를 안 댔다' 고 하면 오탐이다."""
        only_plans = '입사 후 정확하게 해내겠습니다. 기록을 남기는 일에 보탬이 되고 싶습니다.'
        self.assertEqual(C.lint_evidence([], only_plans, self.SRC), [])


class EvidenceInGenerationTest(EngineFixture, unittest.TestCase):
    def draft_with(self, evidence_rows, answer):
        return llm_result(
            '===PLAN===\np1 | 주장 | 요구 | 2024-경험.md | 근거가 주장을 받친다 | - | -\n===END===\n'
            f'===ANSWER===\n{answer}\n===END===\n'
            '===META===\n참조한 경험: 테스트 경험 (2024-경험.md)\n===END===\n'
            + block(*evidence_rows))

    def test_fabricated_quote_marks_review_required(self):
        answer = '경험 파일 내용을 다루었습니다. ' + '가' * 180
        llm = ScriptedLLM(self.draft_with(
            ['c1 | s1 | 내용을 다뤘다 | action | 2024-경험.md | 원문에 전혀 없는 구절입니다'],
            answer), review())
        done = dict(self.run_events(quality_check=True, complete=llm,
                                    length=bounded(200)))['done']
        self.assertIn(C.ISSUE_EVIDENCE_QUOTE_NOT_FOUND, done['issues'])
        self.assertEqual(done['status'], C.STATUS_REVIEW_REQUIRED)
        self.assertEqual(done['evidence'][0]['verdict'], 'quote_not_found')

    def test_real_quote_is_reported_as_found(self):
        answer = '내용을 다루었습니다. ' + '가' * 180
        llm = ScriptedLLM(self.draft_with(
            ['c1 | s1 | 내용을 다뤘다 | action | 2024-경험.md | 내용'], answer), review())
        done = dict(self.run_events(quality_check=True, complete=llm,
                                    length=bounded(200)))['done']
        self.assertNotIn(C.ISSUE_EVIDENCE_QUOTE_NOT_FOUND, done['issues'])
        self.assertEqual(done['evidence'][0]['verdict'], 'quote_found')

    def test_prompt_asks_for_evidence_only_when_checking(self):
        llm = ScriptedLLM(self.draft_with([], '가' * 90), review())
        self.run_events(quality_check=True, complete=llm, length=bounded(200))
        self.assertIn('===EVIDENCE===', llm.messages[0][0]['content'])

        llm2 = ScriptedLLM(self.draft_with([], '가' * 90))
        self.run_events(quality_check=False, complete=llm2, length=bounded(200))
        self.assertNotIn('===EVIDENCE===', llm2.messages[0][0]['content'])


if __name__ == '__main__':
    unittest.main()


class NeedsEvidenceTest(unittest.TestCase):
    """실사용에서 발견한 오탐 회귀 방지 (S11-19)."""

    def test_impact_claim_needs_evidence_even_without_a_self_subject(self):
        """주어가 내가 아니어도 영향 주장은 근거가 필요하다 — 가장 지어내기 쉬운 종류다."""
        s = '그 보고서가 담당자들의 업무 순서 기준이 되었습니다.'
        self.assertFalse(C.is_self_claim(s))
        self.assertTrue(C.needs_evidence(s))

    def test_action_claim_needs_evidence(self):
        self.assertTrue(C.needs_evidence('선적서류를 분류하고 데이터를 관리했습니다.'))

    def test_purpose_and_reflection_are_not_factual_claims(self):
        for s in ['제가 무엇을 놓쳤는지 듣기 위해서였습니다.',
                  '처음 하는 사람에게는 가장 자주 틀리는 지점이었습니다.',
                  '매뉴얼을 쓰며 배운 것은 기준이 달라야 한다는 점이었습니다.']:
            self.assertFalse(C.needs_evidence(s), s)

    def test_future_and_motive_are_excluded(self):
        self.assertFalse(C.needs_evidence('정확하게 해내겠습니다.'))
        self.assertFalse(C.needs_evidence('공고를 보고 지원을 결정했습니다.'))
