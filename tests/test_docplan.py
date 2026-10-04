"""문서 전체 배분 계획 (S11-22, 공유받은 자소서 작성 프롬프트 2).

문항 하나만 보면 전부 타당한데 문서로 보면 틀린 것들을 잡는다. 실제로 이 프로젝트에서
한 지원서의 네 문항 중 세 곳에 같은 인턴십 경험이 들어간 적이 있다. 문항별로는 전부
최선의 선택이었다 — 그래서 문항 단위 장치로는 보이지 않았다.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import coverletter as C, runs  # noqa: E402
from test_coverletter import EngineFixture, llm_result  # noqa: E402

SUPPLIED = {'2026-인턴.md', '2026-해커톤.md', '2024-근로.md'}


def plan_text(rows, positioning='이 지원자는 기록을 믿을 수 있게 만드는 사람이다.'):
    body = '===DOCPLAN===\n' + '\n'.join(rows) + '\n===END===\n'
    if positioning is not None:
        body += f'===POSITIONING===\n{positioning}\n===END===\n'
    return body


CLEAN = [
    'q1 | 지원 진정성 | 2026-해커톤.md | 사용자 관점 | 쓸 사람에게 묻고 고친다 | 관심 → 회사 방향 → 연결점',
    'q2 | 성향 | 2024-근로.md | 기록 정확성 | 틀릴 수 있는 단계를 줄인다 | 주장 → 장면 → 결과 → 보완',
    'q3 | 직무 경험 | 2026-인턴.md | 공정 이해 | 앞 기록이 뒤 공정을 좌우한다 | 문제 → 판단 → 행동 → 결과',
    'q4 | 입사 후 계획 | 2026-인턴.md | 표준화 | 누가 맡아도 같은 결과를 만든다 | 직후 → 이후 → 중장기',
]
IDS = ['q1', 'q2', 'q3', 'q4']


class ParseTest(unittest.TestCase):
    def test_parses_rows_and_positioning(self):
        rows, pos = C.parse_document_plan(plan_text(CLEAN))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]['experiences'], ['2026-해커톤.md'])
        self.assertIn('기록', pos)

    def test_no_block_is_not_an_error(self):
        rows, pos = C.parse_document_plan('아무 말')
        self.assertEqual((rows, pos), ([], ''))

    def test_two_experiences_in_one_question(self):
        rows, _ = C.parse_document_plan(plan_text(
            ['q1 | x | 2026-인턴.md, 2024-근로.md | 역량 | 메시지 | 흐름']))
        self.assertEqual(rows[0]['experiences'], ['2026-인턴.md', '2024-근로.md'])


class LintTest(unittest.TestCase):
    def lint(self, rows, positioning='포지셔닝 한 문장', ids=None):
        parsed, pos = C.parse_document_plan(plan_text(rows, positioning))
        return C.lint_document_plan(parsed, pos, ids or IDS, SUPPLIED)

    def test_clean_plan_passes(self):
        self.assertEqual(self.lint(CLEAN), [])

    def test_missing_plan(self):
        self.assertEqual(C.lint_document_plan([], '', IDS, SUPPLIED),
                         [C.ISSUE_DOCPLAN_MISSING])

    def test_question_ids_must_match(self):
        self.assertIn(C.ISSUE_DOCPLAN_QUESTION_MISMATCH, self.lint(CLEAN[:3]))

    def test_unknown_experience_file(self):
        rows = list(CLEAN)
        rows[0] = 'q1 | x | 2099-없는파일.md | 역량 | 메시지 | 흐름'
        self.assertIn(C.ISSUE_DOCPLAN_UNKNOWN_EXPERIENCE, self.lint(rows))

    def test_same_experience_in_most_questions_is_flagged(self):
        """2026-10-02 에 실제로 일어난 일 — 네 문항 중 세 곳이 같은 인턴십이었다."""
        rows = [r.replace('2026-해커톤.md', '2026-인턴.md').replace('2024-근로.md', '2026-인턴.md')
                for r in CLEAN[:3]] + [CLEAN[3]]
        self.assertIn(C.ISSUE_DOCPLAN_EXPERIENCE_OVERUSED, self.lint(rows))

    def test_two_of_four_is_allowed(self):
        """같은 경험을 두 문항에 쓰는 것 자체는 금지가 아니다."""
        self.assertNotIn(C.ISSUE_DOCPLAN_EXPERIENCE_OVERUSED, self.lint(CLEAN))

    def test_same_strength_everywhere(self):
        rows = [r.split(' | ')[0] + ' | x | 2026-인턴.md | 기록 정확성 | 메시지 | 흐름' + str(i)
                for i, r in enumerate(CLEAN)]
        self.assertIn(C.ISSUE_DOCPLAN_SAME_STRENGTH, self.lint(rows))

    def test_empty_core_message(self):
        rows = list(CLEAN)
        rows[1] = 'q2 | 성향 | 2024-근로.md | 기록 정확성 | - | 주장 → 장면'
        self.assertIn(C.ISSUE_DOCPLAN_NO_MESSAGE, self.lint(rows))

    def test_identical_flow_in_every_question(self):
        """모든 문항을 똑같이 STAR 로 찍어내지 않는다."""
        rows = [f'q{i} | 확인 | 2026-인턴.md | 역량{i} | 메시지{i} | S-T-A-R'
                for i in range(1, 5)]
        self.assertIn(C.ISSUE_DOCPLAN_UNIFORM_FLOW, self.lint(rows))

    def test_positioning_is_required(self):
        self.assertIn(C.ISSUE_DOCPLAN_NO_POSITIONING, self.lint(CLEAN, positioning=None))


class PlanEventsTest(EngineFixture, unittest.TestCase):
    QUESTIONS = [{'id': 'q1', 'text': '지원 동기', 'limit': 500},
                 {'id': 'q2', 'text': '성격의 장단점', 'limit': None}]

    def run_plan(self, text):
        calls = []

        def fake(system=None, messages=None, max_tokens=None, on_delta=None):
            calls.append(system)
            return llm_result(text)

        events = list(C.plan_document_events(
            '홍길동', self.QUESTIONS, self.exp_dir, company='기업', position='직무',
            context=self.ctx(), db_path=self.db_path, surface='web', complete=fake))
        return dict(events), calls

    def test_one_llm_call_for_the_whole_document(self):
        _, calls = self.run_plan(plan_text([
            'q1 | 진정성 | 2024-경험.md | 리더십 | 메시지 하나 | 관심 → 연결',
            'q2 | 성향 | 2024-경험.md | 꼼꼼함 | 메시지 둘 | 주장 → 장면']))
        self.assertEqual(len(calls), 1)
        self.assertIn('문서 전체 배분 계획', calls[0])

    def test_assignments_are_usable_as_override_filenames(self):
        events, _ = self.run_plan(plan_text([
            'q1 | 진정성 | 2024-경험.md | 리더십 | 메시지 하나 | 관심 → 연결',
            'q2 | 성향 | 2024-경험.md | 꼼꼼함 | 메시지 둘 | 주장 → 장면']))
        self.assertEqual(events['done']['assignments'],
                         {'q1': ['2024-경험.md'], 'q2': ['2024-경험.md']})

    def test_plan_run_is_recorded(self):
        events, _ = self.run_plan(plan_text([
            'q1 | 진정성 | 2024-경험.md | 리더십 | 메시지 하나 | 관심 → 연결',
            'q2 | 성향 | 2024-경험.md | 꼼꼼함 | 메시지 둘 | 주장 → 장면']))
        run = runs.get(events['done']['run_id'], db_path=self.db_path)
        self.assertEqual(run['workflow'], 'coverletter.plan_document')

    def test_missing_plan_block_marks_review_required(self):
        events, _ = self.run_plan('계획을 세우지 않고 아무 말')
        self.assertEqual(events['done']['status'], C.STATUS_REVIEW_REQUIRED)
        self.assertIn(C.ISSUE_DOCPLAN_MISSING, events['done']['issues'])

    def test_no_questions_is_an_error(self):
        events = list(C.plan_document_events(
            '홍길동', [], self.exp_dir, company='기업', position='직무',
            context=self.ctx(), db_path=self.db_path, complete=lambda **kw: None))
        self.assertEqual(events[0][0], 'error')


if __name__ == '__main__':
    unittest.main()
