"""S9-1 지원 상태와 이력 테스트. LLM 을 안 쓰는 순수 DB 로직이라 전부 실제 동작을 검증한다."""
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import applications, db  # noqa: E402


class ApplicationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / 'app.db'
        db.init_db(self.db_path)
        conn = db.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json) "
            "VALUES ('manual','0001','테스트기업','백엔드','[]','[]')"
        )
        self.job_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json) "
            "VALUES ('manual','0002','두번째기업','프론트엔드','[]','[]')"
        )
        self.job_id2 = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_invalid_status_rejected(self):
        r = applications.set_status('홍길동', self.job_id, '검토중', db_path=self.db_path)
        self.assertFalse(r.ok)
        self.assertIn('유효하지 않은', r.error)

    def test_first_call_creates_application_with_initial_event(self):
        r = applications.set_status('홍길동', self.job_id, '준비중', db_path=self.db_path)
        self.assertTrue(r.ok)
        self.assertTrue(r.changed)
        self.assertEqual(r.status, '준비중')

        history = applications.get_history('홍길동', self.job_id, db_path=self.db_path)
        self.assertEqual([h.to_status for h in history], ['관심', '준비중'])
        self.assertIsNone(history[0].from_status)
        self.assertEqual(history[1].from_status, '관심')

    def test_same_status_not_recorded_as_change(self):
        applications.set_status('홍길동', self.job_id, '관심', db_path=self.db_path)
        r = applications.set_status('홍길동', self.job_id, '관심', db_path=self.db_path)
        self.assertTrue(r.ok)
        self.assertFalse(r.changed)
        history = applications.get_history('홍길동', self.job_id, db_path=self.db_path)
        self.assertEqual(len(history), 1)  # 최초 등록 이벤트 하나만

    def test_three_status_changes_full_history_visible(self):
        """ROADMAP S9-1 완료 조건 그대로: 상태 3회 변경 후 전체 이력 조회 가능."""
        applications.set_status('홍길동', self.job_id, '준비중', note='서류 준비 시작', db_path=self.db_path)
        applications.set_status('홍길동', self.job_id, '지원완료', note='제출함', db_path=self.db_path)
        applications.set_status('홍길동', self.job_id, '합격', note='최종 합격', db_path=self.db_path)

        history = applications.get_history('홍길동', self.job_id, db_path=self.db_path)
        self.assertEqual([h.to_status for h in history], ['관심', '준비중', '지원완료', '합격'])
        self.assertEqual(history[-1].note, '최종 합격')
        # 이 경험(자소서)이 합격까지 이어졌는지 역산할 수 있어야 한다는 ROADMAP 취지 —
        # from_status 체인이 끊기지 않고 이어지는지 확인
        for prev, cur in zip(history, history[1:]):
            self.assertEqual(cur.from_status, prev.to_status)

    def test_no_duplicate_application_rows_for_same_user_job(self):
        applications.set_status('홍길동', self.job_id, '준비중', db_path=self.db_path)
        applications.set_status('홍길동', self.job_id, '지원완료', db_path=self.db_path)
        conn = db.get_conn(self.db_path)
        count = conn.execute('SELECT COUNT(*) AS n FROM applications WHERE user=? AND job_id=?',
                             ('홍길동', self.job_id)).fetchone()['n']
        conn.close()
        self.assertEqual(count, 1)

    def test_history_empty_for_unregistered_application(self):
        self.assertEqual(applications.get_history('홍길동', self.job_id, db_path=self.db_path), [])

    def test_list_applications_joins_job_info_sorted_by_recent(self):
        applications.set_status('홍길동', self.job_id, '준비중', db_path=self.db_path)
        applications.set_status('홍길동', self.job_id2, '관심', db_path=self.db_path)
        applications.set_status('홍길동', self.job_id, '지원완료', db_path=self.db_path)  # job1 을 더 최근에 갱신

        apps = applications.list_applications('홍길동', db_path=self.db_path)
        self.assertEqual(len(apps), 2)
        self.assertEqual(apps[0].job_id, self.job_id)  # 가장 최근 갱신
        self.assertEqual(apps[0].company, '테스트기업')
        self.assertEqual(apps[0].status, '지원완료')

    def test_different_users_do_not_share_application_state(self):
        applications.set_status('홍길동', self.job_id, '지원완료', db_path=self.db_path)
        applications.set_status('김철수', self.job_id, '준비중', db_path=self.db_path)

        hong = applications.list_applications('홍길동', db_path=self.db_path)
        kim = applications.list_applications('김철수', db_path=self.db_path)
        self.assertEqual(hong[0].status, '지원완료')
        self.assertEqual(kim[0].status, '준비중')

    def test_list_available_jobs_excludes_registered_and_closed(self):
        applications.set_status('홍길동', self.job_id, '관심', db_path=self.db_path)
        conn = db.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json, closed) "
            "VALUES ('manual','0003','마감된기업','백엔드','[]','[]', 1)")
        conn.commit()
        conn.close()

        available = applications.list_available_jobs('홍길동', db_path=self.db_path)
        companies = [j['company'] for j in available]
        self.assertNotIn('테스트기업', companies)   # 이미 등록됨
        self.assertNotIn('마감된기업', companies)   # 마감됨
        self.assertIn('두번째기업', companies)


class DashboardSummaryTest(unittest.TestCase):
    """S9-2: 마감 임박 / 준비중 / 결과 대기 세 갈래 요약."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / 'app.db'
        db.init_db(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def insert_job(self, company, position, deadline=None):
        conn = db.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json, deadline) "
            "VALUES ('manual', ?, ?, ?, '[]', '[]', ?)",
            (company, company, position, deadline),
        )
        job_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        conn.commit()
        conn.close()
        return job_id

    def test_closing_soon_within_window_only(self):
        soon = (date.today() + timedelta(days=3)).isoformat()
        far = (date.today() + timedelta(days=30)).isoformat()
        past = (date.today() - timedelta(days=1)).isoformat()
        j_soon = self.insert_job('임박기업', '직무', deadline=soon)
        j_far = self.insert_job('여유기업', '직무', deadline=far)
        j_past = self.insert_job('마감된기업', '직무', deadline=past)
        for j in (j_soon, j_far, j_past):
            applications.set_status('홍길동', j, '관심', db_path=self.db_path)

        summary = applications.dashboard_summary('홍길동', db_path=self.db_path)
        companies = [a.company for a in summary['closing_soon']]
        self.assertEqual(companies, ['임박기업'])

    def test_no_deadline_excluded_from_closing_soon(self):
        j = self.insert_job('상시기업', '직무', deadline=None)
        applications.set_status('홍길동', j, '관심', db_path=self.db_path)
        summary = applications.dashboard_summary('홍길동', db_path=self.db_path)
        self.assertEqual(summary['closing_soon'], [])

    def test_terminal_status_excluded_from_closing_soon_even_if_near(self):
        soon = (date.today() + timedelta(days=1)).isoformat()
        j = self.insert_job('합격기업', '직무', deadline=soon)
        applications.set_status('홍길동', j, '합격', db_path=self.db_path)
        summary = applications.dashboard_summary('홍길동', db_path=self.db_path)
        self.assertEqual(summary['closing_soon'], [])

    def test_in_progress_and_awaiting_result_grouped_correctly(self):
        j1 = self.insert_job('준비중기업', '직무')
        j2 = self.insert_job('제출기업', '직무')
        j3 = self.insert_job('대기기업', '직무')
        j4 = self.insert_job('관심기업', '직무')
        applications.set_status('홍길동', j1, '준비중', db_path=self.db_path)
        applications.set_status('홍길동', j2, '지원완료', db_path=self.db_path)
        applications.set_status('홍길동', j3, '대기', db_path=self.db_path)
        applications.set_status('홍길동', j4, '관심', db_path=self.db_path)

        summary = applications.dashboard_summary('홍길동', db_path=self.db_path)
        self.assertEqual([a.company for a in summary['in_progress']], ['준비중기업'])
        self.assertEqual(
            sorted(a.company for a in summary['awaiting_result']), ['대기기업', '제출기업'])

    def test_closing_soon_sorted_by_nearest_deadline(self):
        d5 = (date.today() + timedelta(days=5)).isoformat()
        d1 = (date.today() + timedelta(days=1)).isoformat()
        j5 = self.insert_job('5일후', '직무', deadline=d5)
        j1 = self.insert_job('1일후', '직무', deadline=d1)
        applications.set_status('홍길동', j5, '관심', db_path=self.db_path)
        applications.set_status('홍길동', j1, '관심', db_path=self.db_path)

        summary = applications.dashboard_summary('홍길동', db_path=self.db_path)
        self.assertEqual([a.company for a in summary['closing_soon']], ['1일후', '5일후'])


if __name__ == '__main__':
    unittest.main()
