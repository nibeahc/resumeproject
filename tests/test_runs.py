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
