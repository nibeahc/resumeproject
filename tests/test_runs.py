"""lib/runs.py — 실행 기록 (S11-17, 2차 설계검토 4장).

이 테스트가 지키려는 명제는 하나다.
**"공통 실행기가 있다" 가 아니라 "이 문서가 공통 실행기를 거쳤다" 를 되물을 수 있어야 한다.**

2026-10-02 에 실제 자소서가 lib/coverletter.py 를 거치지 않고 versions.save_version 만
불러 저장됐는데 이틀 동안 아무도 몰랐다. documents.run_id 가 비어 있다는 사실로
그걸 알 수 있어야 한다.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import coverletter as C, db, runs, versions  # noqa: E402
from test_coverletter import EngineFixture, bounded, fake_complete  # noqa: E402


class ContentHashTest(unittest.TestCase):
    def test_same_text_same_hash(self):
        self.assertEqual(runs.content_hash('가나다'), runs.content_hash('가나다'))

    def test_one_character_changes_hash(self):
        self.assertNotEqual(runs.content_hash('가나다'), runs.content_hash('가나라'))

    def test_empty_and_none_are_handled(self):
        self.assertEqual(runs.content_hash(''), runs.content_hash(None))


class RecordTest(EngineFixture, unittest.TestCase):
    def rec(self, **kw):
        base = dict(run_id=runs.new_run_id(), user='홍길동', surface='web',
                    workflow='coverletter.generate', content_hash=runs.content_hash('본문'),
                    status=C.STATUS_READY, issues=['over_limit'],
                    quality_check=True, reviewed=True, inputs={'experiences': ['a.md']})
        base.update(kw)
        return runs.RunRecord(**base)

    def test_roundtrip_keeps_lists_and_flags(self):
        r = self.rec()
        runs.record(r, db_path=self.db_path)
        got = runs.get(r.run_id, db_path=self.db_path)
        self.assertEqual(got['issues'], ['over_limit'])
        self.assertEqual(got['inputs'], {'experiences': ['a.md']})
        self.assertIs(got['quality_check'], True)
        self.assertIs(got['reviewed'], True)
        self.assertEqual(got['surface'], 'web')

    def test_same_run_id_updates_instead_of_duplicating(self):
        r = self.rec(status=C.STATUS_READY)
        runs.record(r, db_path=self.db_path)
        r.status = C.STATUS_REVIEW_REQUIRED
        runs.record(r, db_path=self.db_path)
        self.assertEqual(runs.get(r.run_id, db_path=self.db_path)['status'],
                         C.STATUS_REVIEW_REQUIRED)
        conn = db.get_conn(self.db_path)
        try:
            self.assertEqual(conn.execute('SELECT COUNT(*) c FROM runs').fetchone()['c'], 1)
        finally:
            conn.close()

    def test_unknown_surface_is_rejected(self):
        with self.assertRaises(ValueError):
            runs.record(self.rec(surface='텔레파시'), db_path=self.db_path)

    def test_unknown_run_id_returns_none(self):
        self.assertIsNone(runs.get('없는아이디', db_path=self.db_path))


class VerifyTest(EngineFixture, unittest.TestCase):
    """검사 결과는 **그때 그 본문**에 대한 것이다. 본문이 바뀌면 더 이상 유효하지 않다."""

    def setUp(self):
        super().setUp()
        self.rid = runs.new_run_id()
        runs.record(runs.RunRecord(
            run_id=self.rid, user='홍길동', surface='cli', workflow='coverletter.generate',
            content_hash=runs.content_hash('검사한 본문'), status=C.STATUS_READY),
            db_path=self.db_path)

    def test_unchanged_text_verifies(self):
        v = runs.verify(self.rid, '검사한 본문', db_path=self.db_path)
        self.assertTrue(v['found'])
        self.assertTrue(v['unchanged'])
        self.assertFalse(v['stale'])

    def test_edited_after_check_is_stale(self):
        v = runs.verify(self.rid, '검사한 본문 + 나중에 손으로 고친 문장', db_path=self.db_path)
        self.assertTrue(v['found'])
        self.assertTrue(v['stale'])

    def test_missing_run_is_not_found(self):
        v = runs.verify('없는아이디', '아무 본문', db_path=self.db_path)
        self.assertFalse(v['found'])
        self.assertIsNone(v['run'])


class GenerationLeavesRunRecordTest(EngineFixture, unittest.TestCase):
    def test_saved_document_points_at_its_run(self):
        events = dict(self.run_events(surface='web'))
        done = events['done']
        self.assertTrue(done['run_id'])
        run = runs.get(done['run_id'], db_path=self.db_path)
        self.assertEqual(run['surface'], 'web')
        self.assertEqual(run['workflow'], 'coverletter.generate')
        self.assertEqual(run['user'], '홍길동')

    def test_document_row_carries_run_id(self):
        done = dict(self.run_events())['done']
        conn = db.get_conn(self.db_path)
        try:
            row = conn.execute('SELECT run_id, path FROM documents ORDER BY id DESC LIMIT 1').fetchone()
        finally:
            conn.close()
        self.assertEqual(row['run_id'], done['run_id'])
        self.assertEqual(runs.for_document(row['path'], db_path=self.db_path)['run_id'],
                         done['run_id'])

    def test_hand_saved_document_has_no_run(self):
        """손으로 저장하면 run_id 가 비고, 그 사실이 곧 '실행기를 안 거쳤다' 는 기록이다.
        2026-10-02 퀴네앤드나겔 자소서가 정확히 이 상태였다."""
        vr = versions.save_version(self.out, '기업_직무/자소서.md', '손으로 쓴 본문',
                                   user='홍길동', db_path=self.db_path)
        self.assertTrue(vr.ok)
        self.assertIsNone(runs.for_document(vr.path, db_path=self.db_path))

    def test_run_record_hashes_the_checked_answer(self):
        done = dict(self.run_events())['done']
        run = runs.get(done['run_id'], db_path=self.db_path)
        self.assertEqual(run['content_hash'], runs.content_hash(done['answer']))
        self.assertTrue(runs.verify(done['run_id'], done['answer'],
                                    db_path=self.db_path)['unchanged'])


class NotCheckedIsNotPassedTest(EngineFixture, unittest.TestCase):
    """검사 통과 / 검사 실패 / **검사하지 않음** 은 서로 다른 상태다 (2차 설계검토 4-3)."""

    def test_toggle_off_reports_not_checked(self):
        done = dict(self.run_events(quality_check=False))['done']
        self.assertFalse(done['quality_checked'])
        run = runs.get(done['run_id'], db_path=self.db_path)
        self.assertFalse(run['quality_check'])
        self.assertFalse(run['reviewed'])

    def test_revise_records_that_critic_did_not_run(self):
        self.run_events()
        events = list(C.revise_coverletter_events(
            '홍길동', '지원 동기', self.exp_dir, rel_path='기업_직무/자소서.md',
            instruction='더 짧게', length=bounded(100), context=self.ctx(),
            output_dir=self.out, db_path=self.db_path, surface='web',
            complete=fake_complete()))
        done = dict(events)['done']
        self.assertFalse(done['quality_checked'])
        run = runs.get(done['run_id'], db_path=self.db_path)
        self.assertEqual(run['workflow'], 'coverletter.revise')
        self.assertFalse(run['reviewed'])


class DefaultDbGuardTest(unittest.TestCase):
    """테스트가 실제 DB 로 폴백하는 것을 코드가 막는다 (2차 설계검토 5-2)."""

    def test_default_path_is_blocked_during_tests(self):
        with self.assertRaises(RuntimeError):
            db.get_conn()

    def test_allow_default_db_opens_and_closes_the_door(self):
        with db.allow_default_db():
            conn = db.get_conn()
            conn.close()
        with self.assertRaises(RuntimeError):
            db.get_conn()


if __name__ == '__main__':
    unittest.main()


class SourceScopeTest(unittest.TestCase):
    """출처마다 증명할 수 있는 것이 다르다 (S11-18, 2차 설계검토 3-1).

    핵심 명제: **어딘가에 같은 단어가 있는 것과 그 주장이 참인 것은 다르다.**
    """

    def srcs(self):
        return [C.Source('experience', '창고에서 120건의 입출고를 확인하였다'),
                C.Source('job', '연간 2500만 건 처리, SAP/WMS 활용, 30kg 이하 화물'),
                C.Source('company', '한국 법인 직원 246명')]

    def test_own_number_is_not_flagged(self):
        self.assertEqual(C.find_jd_only_facts('120건을 처리했습니다.', self.srcs()), [])

    def test_number_only_in_job_posting_is_flagged(self):
        out = C.find_jd_only_facts('2500만 건을 처리했습니다.', self.srcs())
        self.assertEqual(len(out), 1)
        self.assertIn('2500', out[0])
        self.assertIn('직무', out[0])

    def test_number_only_in_company_file_is_flagged(self):
        out = C.find_jd_only_facts('직원 246명 규모에서 일했습니다.', self.srcs())
        self.assertTrue(out and '246' in out[0])

    def test_future_plan_is_not_a_factual_claim(self):
        """"입사 후 익히겠습니다" 는 주장이 아니라 계획이다 (검토 3-2)."""
        self.assertEqual(C.find_jd_only_facts('2500만 건을 처리하겠습니다.', self.srcs()), [])

    def test_find_unverified_facts_still_accepts_plain_strings(self):
        nums, orgs = C.find_unverified_facts('37%를 줄였습니다.', ['내용에 숫자 없음'])
        self.assertEqual(nums, ['37'])


class JdTermClaimTest(unittest.TestCase):
    """검토 3-1 의 SAP 예시. 공고에 SAP 가 있다고 SAP 경험이 생기지는 않는다."""

    EXPERIENCES = ['엑셀로 예약 대장을 관리하였다']

    def test_extracts_tool_names_from_requirements(self):
        self.assertEqual(C.jd_terms(['SAP/WMS 활용 경험', 'Excel 중급 이상']),
                         ['SAP', 'WMS', 'Excel'])

    def test_common_english_words_are_not_terms(self):
        self.assertEqual(C.jd_terms(['experience with and of the']), ['experience'])

    def test_claiming_a_jd_tool_without_evidence_is_flagged(self):
        out = C.check_jd_claims('SAP로 재고를 관리했습니다.', ['SAP', 'WMS'], self.EXPERIENCES)
        self.assertEqual(out, ['SAP'])

    def test_intent_to_learn_is_allowed(self):
        out = C.check_jd_claims('입사 후 SAP를 익히겠습니다.', ['SAP'], self.EXPERIENCES)
        self.assertEqual(out, [])

    def test_tool_present_in_experience_passes(self):
        out = C.check_jd_claims('SAP로 재고를 관리했습니다.', ['SAP'],
                                ['SAP 모듈로 재고를 조회하였다'])
        self.assertEqual(out, [])


class JdScopeInGenerationTest(EngineFixture, unittest.TestCase):
    def test_generation_flags_unsupported_jd_tool(self):
        job = {'company': '기업', 'position': '직무',
               'required_json': '["SAP 활용 경험", "WMS 운영"]'}
        ctx = self.ctx(job=job)
        answer = 'SAP로 재고를 관리했습니다. ' + '가' * 80
        events = dict(self.run_events(context=ctx, length=bounded(200),
                                      complete=fake_complete(answer=answer)))
        done = events['done']
        self.assertIn(C.ISSUE_JD_TERM_UNSUPPORTED, done['issues'])
        self.assertEqual(done['jd_terms_unsupported'], ['SAP'])
        self.assertEqual(done['status'], C.STATUS_REVIEW_REQUIRED)

    def test_clean_draft_has_no_jd_scope_issue(self):
        job = {'company': '기업', 'position': '직무', 'required_json': '["SAP 활용 경험"]'}
        events = dict(self.run_events(context=self.ctx(job=job), length=bounded(200),
                                      complete=fake_complete(answer='가' * 190)))
        done = events['done']
        self.assertNotIn(C.ISSUE_JD_TERM_UNSUPPORTED, done['issues'])
        self.assertNotIn(C.ISSUE_FACT_FROM_JD_ONLY, done['issues'])


class PerceptionIsNotAClaimTest(unittest.TestCase):
    """실사용 중 발견한 오탐 회귀 방지 (S11-18).

    "퀴네앤드나겔이 2030년까지 가장 신뢰받는 공급망 파트너가 되겠다고 밝힌 것을 보았습니다"
    는 기업 사실을 기업 것으로 제대로 귀속한 문장이다. 무언가를 본 것은 한 것이 아니다.
    """

    SRCS = [C.Source('experience', '창고에서 일하였다'),
            C.Source('company', 'Vision 2030: 가장 신뢰받는 공급망 파트너')]

    def test_seeing_a_company_fact_is_not_a_self_claim(self):
        s = '퀴네앤드나겔이 2030년까지 가장 신뢰받는 공급망 파트너가 되겠다고 밝힌 것을 보았습니다.'
        self.assertFalse(C.is_self_claim(s))
        self.assertEqual(C.find_jd_only_facts(s, self.SRCS), [])

    def test_doing_is_still_a_claim(self):
        self.assertTrue(C.is_self_claim('2030건을 처리했습니다.'))
