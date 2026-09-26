"""S9-1 지원 현황 라우트 테스트. lib.applications 는 순수 DB 로직(LLM 없음)이라 스텁 없이
실제 동작을 검증한다. DB_PATH 는 임시 파일로 격리해 실제 프로젝트 DB 를 건드리지 않는다."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import db  # noqa: E402
from web import create_app  # noqa: E402


class ApplicationsRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        self.db_path = self.tmp / 'app.db'
        self.root.mkdir(parents=True)
        db.init_db(self.db_path)
        conn = db.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json) "
            "VALUES ('manual','0001','테스트기업','백엔드','[]','[]')"
        )
        self.job_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        conn.commit()
        conn.close()
        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.db_path, 'TESTING': True})
        self.c = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_page_renders_with_available_job(self):
        r = self.c.get('/applications/홍길동')
        self.assertEqual(r.status_code, 200)
        self.assertIn('테스트기업'.encode('utf-8'), r.data)
        self.assertIn('아직 등록한 지원이 없습니다'.encode('utf-8'), r.data)

    def test_register_interest_creates_application(self):
        r = self.c.post(f'/applications/홍길동/{self.job_id}/status',
                        json={'status': '관심', 'note': '관심 등록'})
        self.assertTrue(r.get_json()['success'])

        page = self.c.get('/applications/홍길동')
        body = page.get_data(as_text=True)
        self.assertNotIn('아직 등록한 지원이 없습니다', body)
        self.assertIn('테스트기업', body)
        self.assertIn('status-관심', body)

    def test_invalid_status_rejected_with_400(self):
        r = self.c.post(f'/applications/홍길동/{self.job_id}/status', json={'status': '검토중'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()['success'])

    def test_status_change_recorded_and_visible_in_history(self):
        self.c.post(f'/applications/홍길동/{self.job_id}/status', json={'status': '준비중'})
        self.c.post(f'/applications/홍길동/{self.job_id}/status', json={'status': '지원완료'})
        self.c.post(f'/applications/홍길동/{self.job_id}/status', json={'status': '합격', 'note': '최종 합격'})

        r = self.c.get(f'/applications/홍길동/{self.job_id}/history')
        history = r.get_json()['history']
        self.assertEqual([h['to_status'] for h in history], ['관심', '준비중', '지원완료', '합격'])
        self.assertEqual(history[-1]['note'], '최종 합격')

    def test_available_jobs_excludes_already_registered(self):
        self.c.post(f'/applications/홍길동/{self.job_id}/status', json={'status': '관심'})
        r = self.c.get('/applications/홍길동')
        body = r.get_data(as_text=True)
        self.assertIn('등록할 수 있는 새 공고가 없습니다', body)

    def test_does_not_touch_real_project_db(self):
        """DB_PATH 격리 회귀 방지: 이 테스트가 실제 data/app.db 를 안 건드리는지 보장한다."""
        self.c.post(f'/applications/홍길동/{self.job_id}/status', json={'status': '준비중'})
        real_conn = db.get_conn()  # db_path 생략 → 실제 프로젝트 DB
        try:
            count = real_conn.execute(
                "SELECT COUNT(*) AS n FROM applications WHERE user = '홍길동'"
            ).fetchone()['n']
        finally:
            real_conn.close()
        self.assertEqual(count, 0)


if __name__ == '__main__':
    unittest.main()
