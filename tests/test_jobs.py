"""S4-3 공고 등록 UI 라우트 테스트. lib.job_parser 는 스텁으로 대체하고,
DB_PATH 를 임시 파일로 주입해 실제 프로젝트 data/app.db 를 절대 건드리지 않는다.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import db  # noqa: E402
from lib.job_parser import JobResult  # noqa: E402
from web import create_app  # noqa: E402
from web.routes import jobs as jobs_route  # noqa: E402


class JobsRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        self.db_path = self.tmp / 'app.db'
        (self.root).mkdir(parents=True)
        db.init_db(self.db_path)
        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.db_path, 'TESTING': True})
        self.c = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_jobs_page_renders_with_no_jobs(self):
        r = self.c.get('/jobs')
        self.assertEqual(r.status_code, 200)
        self.assertIn('등록된 공고가 없습니다'.encode('utf-8'), r.data)

    def test_preview_does_not_touch_real_project_db(self):
        """이 테스트 자체가 회귀 방지 장치다: DB_PATH 를 안 넣으면 실제 data/app.db 를 건드린다."""
        with patch.object(jobs_route.job_parser, 'preview', return_value=JobResult(
                ok=True, company='테스트기업', position='백엔드', required=['조건1'], preferred=[],
                markdown='# 테스트기업 백엔드', raw_text='x' * 60, content_hash='abc')):
            r = self.c.post('/jobs/preview', json={'raw_text': 'x' * 60})
        data = r.get_json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['company'], '테스트기업')
        # 임시 DB 에는 아직 아무 것도 안 썼어야 한다 (preview 는 저장하지 않는다)
        conn = db.get_conn(self.db_path)
        self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM jobs').fetchone()['n'], 0)
        conn.close()

    def test_preview_failure_returned_as_json_not_500(self):
        with patch.object(jobs_route.job_parser, 'preview',
                           return_value=JobResult(ok=False, error='본문이 너무 짧습니다.')):
            r = self.c.post('/jobs/preview', json={'raw_text': '짧음'})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertFalse(data['ok'])
        self.assertIn('짧습니다', data['error'])

    def test_save_commits_preview_payload(self):
        preview_payload = {
            'ok': True, 'duplicate': False, 'company': '테스트기업', 'position': '백엔드',
            'deadline': '2026-12-31', 'employment_type': '정규직',
            'required': ['조건1', '조건2'], 'preferred': ['우대1'],
            'markdown': '# 테스트기업 백엔드\n\n## 메타\n\n- 출처: manual\n- 공고 ID: manual-0001\n- 수집일: 2020.01.01\n',
            'raw_text': 'x' * 60, 'content_hash': 'deadbeef', 'url': '',
        }
        with patch.object(jobs_route.job_parser, 'commit', return_value=JobResult(
                ok=True, saved=True, job_id=1, source_job_id='0001', path='jobs/manual-0001.md',
                company='테스트기업', position='백엔드', required=['조건1', '조건2'], preferred=['우대1'])):
            r = self.c.post('/jobs/save', json=preview_payload)
        data = r.get_json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['saved'])
        self.assertEqual(data['path'], 'jobs/manual-0001.md')

    def test_save_without_raw_text_rejected(self):
        r = self.c.post('/jobs/save', json={'markdown': '# 뭔가', 'company': 'x', 'position': 'y'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()['ok'])

    def test_save_without_markdown_rejected(self):
        r = self.c.post('/jobs/save', json={'raw_text': 'x' * 60, 'company': 'x', 'position': 'y'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()['ok'])

    def test_user_edited_fields_passed_through_to_commit(self):
        """확인 화면에서 회사명/필수조건을 고쳤다면 그 값 그대로 commit() 에 전달돼야 한다."""
        captured = {}

        def fake_commit(preview_result, jobs_dir=None, db_path=None):
            captured['company'] = preview_result.company
            captured['required'] = preview_result.required
            return JobResult(ok=True, saved=True, job_id=1, company=preview_result.company,
                              position=preview_result.position, required=preview_result.required,
                              preferred=preview_result.preferred)

        with patch.object(jobs_route.job_parser, 'commit', side_effect=fake_commit):
            r = self.c.post('/jobs/save', json={
                'raw_text': 'x' * 60, 'markdown': '# 원문', 'company': '사용자가고친회사',
                'position': '백엔드', 'required': ['사용자가 추가한 조건'], 'preferred': [],
                'content_hash': 'abc',
            })
        self.assertTrue(r.get_json()['ok'])
        self.assertEqual(captured['company'], '사용자가고친회사')
        self.assertEqual(captured['required'], ['사용자가 추가한 조건'])

    def test_jobs_list_reflects_real_db_rows(self):
        """job_parser 를 거치지 않고 DB 에 직접 행을 넣어 목록 렌더링만 검증한다."""
        conn = db.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, deadline, employment_type, "
            "required_json, preferred_json, raw_path, content_hash) VALUES "
            "('manual','0001','목록테스트기업','프론트엔드',NULL,'정규직','[\"a\",\"b\"]','[\"c\"]',"
            "'jobs/manual-0001.md','hash1')"
        )
        conn.commit()
        conn.close()

        r = self.c.get('/jobs')
        self.assertIn('목록테스트기업'.encode('utf-8'), r.data)
        self.assertIn('프론트엔드'.encode('utf-8'), r.data)


if __name__ == '__main__':
    unittest.main()
