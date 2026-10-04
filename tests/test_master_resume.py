"""마스터 이력서 (S11-24).

기업 맞춤 서류를 쓰기 전에 **가장 먼저** 만드는 문서다. 핵심 명제 둘:

  1. 누락이 없어야 쓸모가 있다 → 조립은 코드가 한다. 모델에게 22건을 주고 "정리해줘"
     하면 몇 건이 조용히 빠지고, 빠졌다는 사실 자체를 알 수 없다
  2. 이력서는 설득하는 글이다 → "열심히 했다 / 많이 배웠다" 는 일을 잘한다는 증거가 아니다
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import master_resume as M, runs  # noqa: E402
from lib import db  # noqa: E402

EXP = """# {title}

## 메타

- 시기: {period}
- 기관명: {org}
- 역할: {role}
- 카테고리: 전문성·직무역량
- 키워드: {keywords}

## 상세 내용 (STAR)

### Action (내가 한 것)
행동.

### Result (결과)
{result}
"""


def summary_text(keywords='기록 정확성, 절차 표준화',
                 summary='물류 실무를 수행했습니다.\n기록 체계를 개선했습니다.\n데이터로 제안했습니다.'):
    return (f'===KEYWORDS===\n{keywords}\n===END===\n'
            f'===SUMMARY===\n{summary}\n===END===\n')


class Fixture:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'users' / '홍길동'
        self.exp = self.root / 'experiences'
        self.exp.mkdir(parents=True)
        self.root.joinpath('profile.md').write_text(
            '# 프로필\n\n## 기본 정보\n- 이름: 홍길동\n- 이메일: a@b.c\n'
            '\n## 어학 / 자격\n- 영어 - TOEIC 900\n', encoding='utf-8')
        self.write('2026-인턴.md', '물류 인턴', '인턴', '2026.01 ~ 2026.02',
                   result='7주간 120건의 선적서류를 처리하였다.')
        self.write('2025-공모전.md', '수요예측 공모전', '팀원', '2025.09',
                   result='최우수상을 수상하였다. 정확도 92%를 달성하였다.')
        self.write('2024-봉사.md', '지역 봉사', '봉사자', '2024.05',
                   result='많이 배웠고 보람찼다.')
        self.write('2024-특강.md', 'AI 특강', '수강생', '2024.07',
                   result='웹앱을 완성하였다.')
        self.db_path = self.tmp / 'app.db'
        db.init_db(self.db_path)

    def write(self, filename, title, role, period, org='테스트기관',
              keywords='물류, 데이터', result='결과.'):
        (self.exp / filename).write_text(
            EXP.format(title=title, period=period, org=org, role=role,
                       keywords=keywords, result=result), encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def collect(self):
        return M.collect(self.root, self.exp)


class ClassifyTest(Fixture, unittest.TestCase):
    def section_of(self, filename):
        return {e.filename: e.section for e in self.collect().entries}[filename]

    def test_intern_goes_to_work_experience(self):
        self.assertEqual(self.section_of('2026-인턴.md'), M.SECTION_EXPERIENCE)

    def test_competition_goes_to_projects(self):
        self.assertEqual(self.section_of('2025-공모전.md'), M.SECTION_PROJECT)

    def test_volunteering_goes_to_activities(self):
        self.assertEqual(self.section_of('2024-봉사.md'), M.SECTION_ACTIVITY)

    def test_lecture_goes_to_education(self):
        self.assertEqual(self.section_of('2024-특강.md'), M.SECTION_EDUCATION)


class CollectTest(Fixture, unittest.TestCase):
    def test_reads_profile_contact_and_language(self):
        lines = self.collect().profile_lines
        self.assertIn('이름: 홍길동', lines)
        self.assertIn('영어 - TOEIC 900', lines)

    def test_reads_period_org_role_from_meta(self):
        """selector.Experience 는 이 셋을 안 들고 있어서 여기서 따로 읽는다."""
        e = {x.filename: x for x in self.collect().entries}['2026-인턴.md']
        self.assertEqual(e.period, '2026.01 ~ 2026.02')
        self.assertEqual(e.org, '테스트기관')
        self.assertEqual(e.role, '인턴')

    def test_nothing_is_dropped(self):
        self.assertEqual(len(self.collect().entries), 4)

    def test_newest_first(self):
        self.assertEqual(self.collect().entries[0].filename, '2026-인턴.md')


class LintTest(Fixture, unittest.TestCase):
    def test_entry_without_numbers_is_flagged(self):
        r = self.collect()
        self.assertIn('result_without_outcome', M.lint(r))
        self.assertIn('2024-봉사.md', M.needs_numbers(r))

    def test_entry_with_numbers_is_not_flagged(self):
        self.assertNotIn('2026-인턴.md', M.needs_numbers(self.collect()))

    def test_vague_reflection_is_flagged(self):
        """"많이 배웠다 / 보람찼다" 는 일을 잘한다는 증거가 아니다."""
        r = self.collect()
        self.assertIn('vague_reflection', M.lint(r))
        flagged = {v['filename'] for v in M.vague_entries(r)}
        self.assertEqual(flagged, {'2024-봉사.md'})

    def test_no_work_experience_is_flagged(self):
        (self.exp / '2026-인턴.md').unlink()
        self.assertIn('no_work_experience', M.lint(self.collect()))


class SummaryTest(unittest.TestCase):
    def test_parses_keywords_and_summary(self):
        ks, s = M.parse_summary(summary_text())
        self.assertEqual(ks, ['기록 정확성', '절차 표준화'])
        self.assertEqual(len(s.split('\n')), 3)

    def test_clean_summary_passes(self):
        self.assertEqual(M.lint_summary(*M.parse_summary(summary_text())), [])

    def test_too_many_keywords(self):
        ks, s = M.parse_summary(summary_text(keywords='a, b, c, d'))
        self.assertIn('summary_keyword_count', M.lint_summary(ks, s))

    def test_too_few_lines(self):
        ks, s = M.parse_summary(summary_text(summary='한 줄뿐입니다.'))
        self.assertIn('summary_line_count', M.lint_summary(ks, s))

    def test_reflection_in_summary_is_flagged(self):
        ks, s = M.parse_summary(summary_text(
            summary='열심히 했습니다.\n많이 배웠습니다.\n보람찼습니다.'))
        self.assertIn('summary_vague', M.lint_summary(ks, s))

    def test_missing_blocks(self):
        self.assertEqual(sorted(M.lint_summary(*M.parse_summary('없음'))),
                         ['summary_missing', 'summary_no_keywords'])


class MarkdownTest(Fixture, unittest.TestCase):
    def md(self):
        return M.build_markdown(self.collect(), summary='요약입니다.',
                                keywords=['기록 정확성', '절차 표준화'])

    def test_has_every_section_that_has_entries(self):
        md = self.md()
        for title in ('경력 및 업무 경험', '프로젝트 및 성과', '대외활동', '교육 및 학습'):
            self.assertIn(title, md)

    def test_says_it_is_not_for_submission(self):
        self.assertIn('제출용이 아닙니다', self.md())

    def test_marks_entries_that_need_a_concrete_result(self):
        self.assertIn('구체적 결과가 안 보임', self.md())

    def test_every_entry_links_back_to_its_source_file(self):
        md = self.md()
        for f in ('2026-인턴.md', '2025-공모전.md', '2024-봉사.md', '2024-특강.md'):
            self.assertIn(f, md)


class CustomizeTest(Fixture, unittest.TestCase):
    def test_job_relevant_entries_come_first(self):
        """경험 갯수가 아니라 직무와의 연결이 기준이다."""
        got = M.customize(self.collect(), self.exp,
                          job_text='물류 창고 운영, 선적서류 처리', top=4)
        self.assertTrue(got[0]['jd_matched'])
        self.assertGreater(got[0]['score'], got[-1]['score'])


class EventsTest(Fixture, unittest.TestCase):
    def run_build(self, text=None, raises=False, write=False):
        calls = []

        def fake(system=None, messages=None, max_tokens=None, on_delta=None):
            calls.append((system, messages))
            if raises:
                raise RuntimeError('호출 실패')
            return {'text': text if text is not None else summary_text()}

        events = dict(M.build_master_resume_events(
            '홍길동', self.root, self.exp, db_path=self.db_path, surface='web',
            write=write, complete=fake))
        return events, calls

    def test_one_llm_call_with_its_own_skill(self):
        _, calls = self.run_build()
        self.assertEqual(len(calls), 1)
        self.assertIn('마스터 이력서', calls[0][0])

    def test_assembles_every_entry(self):
        events, _ = self.run_build()
        self.assertEqual(events['done']['entries'], 4)

    def test_summary_failure_still_produces_the_document(self):
        """요약을 못 받아도 항목 조립본은 쓸모가 있다."""
        events, _ = self.run_build(raises=True)
        self.assertIn('summary_call_failed', events['done']['issues'])
        self.assertIn('경력 및 업무 경험', events['done']['markdown'])

    def test_writes_the_file_when_asked(self):
        events, _ = self.run_build(write=True)
        path = self.root / M.MASTER_FILENAME
        self.assertTrue(path.exists())
        self.assertEqual(events['done']['saved_to'], 'users/홍길동/마스터이력서.md')

    def test_does_not_write_by_default(self):
        self.run_build()
        self.assertFalse((self.root / M.MASTER_FILENAME).exists())

    def test_run_is_recorded(self):
        events, _ = self.run_build()
        run = runs.get(events['done']['run_id'], db_path=self.db_path)
        self.assertEqual(run['workflow'], 'master_resume.build')

    def test_no_experiences_is_an_error(self):
        for f in self.exp.glob('*.md'):
            f.unlink()
        events = dict(M.build_master_resume_events(
            '홍길동', self.root, self.exp, db_path=self.db_path,
            write=False, complete=lambda **kw: None))
        self.assertIn('error', events)


if __name__ == '__main__':
    unittest.main()


class MasterResumeRouteTest(unittest.TestCase):
    """S11-24: 화면 경로. 미리보기는 파일을 쓰지 않는다 — 사람이 보고 저장을 누른다."""

    def setUp(self):
        from web import create_app
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        u = self.root / 'users' / '홍길동'
        (u / 'experiences').mkdir(parents=True)
        (u / 'experiences' / '2026-인턴.md').write_text(
            EXP.format(title='물류 인턴', period='2026.01', org='기관', role='인턴',
                       keywords='물류', result='120건을 처리하였다.'), encoding='utf-8')
        (u / 'profile.md').write_text('# 프로필\n\n- 이름: 홍길동\n', encoding='utf-8')
        self.user_root = u
        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.tmp / 'app.db',
                           'TESTING': True})
        self.c = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_page_shows_entry_count(self):
        body = self.c.get('/master-resume/홍길동').get_data(as_text=True)
        self.assertIn('마스터 이력서', body)
        self.assertIn('경험 파일 1건', body)

    def test_page_says_it_is_not_for_submission(self):
        self.assertIn('제출용이 아닙니다',
                      self.c.get('/master-resume/홍길동').get_data(as_text=True))

    def test_preview_does_not_write_the_file(self):
        from unittest.mock import patch
        import web.routes.master_resume as route
        with patch.object(route, 'stream_complete', return_value={'text': summary_text()}):
            body = self.c.post('/master-resume/홍길동', json={'write': False}).get_data(as_text=True)
        self.assertIn('event: done', body)
        self.assertFalse((self.user_root / M.MASTER_FILENAME).exists())

    def test_save_writes_the_file(self):
        from unittest.mock import patch
        import web.routes.master_resume as route
        with patch.object(route, 'stream_complete', return_value={'text': summary_text()}):
            # SSE 응답은 **본문을 읽어야** 제너레이터가 돈다. 안 읽으면 아무 일도 안 일어난다.
            self.c.post('/master-resume/홍길동', json={'write': True}).get_data()
        saved = (self.user_root / M.MASTER_FILENAME).read_text(encoding='utf-8')
        self.assertIn('경력 및 업무 경험', saved)
        self.assertIn('물류 인턴', saved)

    def test_path_traversal_in_name_is_rejected(self):
        self.assertIn(self.c.get('/master-resume/..%2f..%2fetc').status_code, (400, 404))


class ConcreteResultTest(Fixture, unittest.TestCase):
    """실사용 중 발견 (2026-10-04). 숫자만 성과로 보면 "최우수상(중부발전사장상) 수상" 과
    "MOU 체결" 이 '정량 성과 없음' 으로 잡힌다. 검사 이름이 틀렸던 것이다."""

    def needs(self):
        return set(M.needs_numbers(self.collect()))

    def test_award_without_digits_counts_as_a_result(self):
        self.write('2024-수상.md', '경진대회', '팀장', '2024.05',
                   result='최우수상(중부발전사장상) 수상.')
        self.assertNotIn('2024-수상.md', self.needs())

    def test_mou_and_paper_count_as_results(self):
        self.write('2024-협약.md', '프로그램 개발', '담당', '2024.11',
                   result='한국중부발전과 MOU를 체결하였다. KCI 논문에 제4저자로 등재되었다.')
        self.assertNotIn('2024-협약.md', self.needs())

    def test_soft_praise_is_still_flagged(self):
        self.write('2024-평가.md', '멘토링', '기획', '2024.08',
                   result='가장 긍정적인 평가를 받았다.')
        self.assertIn('2024-평가.md', self.needs())

    def test_user_can_waive_the_requirement(self):
        """모든 경험에 결과가 필요한 것은 아니다. 지울 수 없는 경고는 노이즈가 된다."""
        (self.exp / '2024-면제.md').write_text(
            EXP.format(title='분석 수행', period='2024.03', org='기관', role='담당',
                       keywords='분석', result='분석을 수행하고 개선 지점을 제안하였다.')
            .replace('- 역할: 담당', '- 역할: 담당\n- 정량성과: 해당없음'), encoding='utf-8')
        self.assertIn('2024-면제.md', {e.filename for e in self.collect().entries})
        self.assertNotIn('2024-면제.md', self.needs())

    def test_waived_entry_shows_no_warning_in_markdown(self):
        (self.exp / '2024-면제.md').write_text(
            EXP.format(title='분석 수행', period='2024.03', org='기관', role='담당',
                       keywords='분석', result='분석을 수행하였다.')
            .replace('- 역할: 담당', '- 역할: 담당\n- 정량성과: 해당없음'), encoding='utf-8')
        md = M.build_markdown(self.collect())
        self.assertIn('분석 수행', md)
        section = md[md.index('분석 수행'):]
        self.assertNotIn('구체적 결과가 안 보임', section.split('출처:')[0])
