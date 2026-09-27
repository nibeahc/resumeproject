"""고도화(2026-09-27) 자소서 트렌드 화면/트리거 배선 테스트. 실제 web_search API 호출은
전부 스텁으로 막는다 — 특히 TESTING 가드가 실제로 동작하는지가 핵심이다(안 그러면 이
프로젝트의 거의 모든 웹 테스트가 GET /user/<name> 을 거칠 때마다 실제 API 를 호출하게 된다).
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import trends  # noqa: E402
from web import create_app  # noqa: E402
from web.routes import main as main_route  # noqa: E402


class TrendsPageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        self.root.mkdir(parents=True)
        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.tmp / 'app.db', 'TESTING': True})
        self.c = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_renders_empty_state_without_content(self):
        r = self.c.get('/trends')
        self.assertEqual(r.status_code, 200)
        self.assertIn('아직 조사된 트렌드가 없습니다'.encode('utf-8'), r.data)

    def test_renders_existing_trends_content(self):
        trends_dir = self.root / 'references' / 'trends'
        trends_dir.mkdir(parents=True)
        (trends_dir / 'coverletter-trends.md').write_text(
            '# 자소서 작성 트렌드\n\n## 구조\n두괄식 선호', encoding='utf-8')
        r = self.c.get('/trends')
        self.assertIn('두괄식 선호'.encode('utf-8'), r.data)


class BackgroundRefreshWiringTest(unittest.TestCase):
    """GET /user/<name> 이 lib.trends.trigger_background_refresh() 를 올바른 조건에서만 부르는지."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        (self.root / 'users' / '홍길동').mkdir(parents=True)
        self.db_path = self.tmp / 'app.db'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_testing_mode_never_triggers_background_refresh(self):
        """회귀 방지: 이 가드가 없으면 이 프로젝트의 거의 모든 웹 테스트가 실제 API 를 호출한다."""
        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.db_path, 'TESTING': True})
        c = app.test_client()
        with patch.object(main_route.trends, 'trigger_background_refresh') as trigger:
            c.get('/user/홍길동')
        trigger.assert_not_called()

    def test_non_testing_mode_triggers_background_refresh(self):
        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.db_path, 'TESTING': False})
        c = app.test_client()
        with patch.object(main_route.trends, 'trigger_background_refresh') as trigger:
            c.get('/user/홍길동')
        trigger.assert_called_once()

    def test_recent_update_shows_banner(self):
        from datetime import date
        trends_dir = self.root / 'references' / 'trends'
        trends_dir.mkdir(parents=True)
        today = date.today().strftime('%Y-%m-%d')
        (trends_dir / 'coverletter-trends.md').write_text(f'> 최종 갱신일: {today}\n\n내용', encoding='utf-8')

        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.db_path, 'TESTING': True})
        c = app.test_client()
        r = c.get('/user/홍길동')
        self.assertIn('트렌드가 업데이트됐어요'.encode('utf-8'), r.data)

    def test_old_update_does_not_show_banner(self):
        from datetime import date, timedelta
        trends_dir = self.root / 'references' / 'trends'
        trends_dir.mkdir(parents=True)
        old = (date.today() - timedelta(days=60)).strftime('%Y-%m-%d')
        (trends_dir / 'coverletter-trends.md').write_text(f'> 최종 갱신일: {old}\n\n내용', encoding='utf-8')

        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.db_path, 'TESTING': True})
        c = app.test_client()
        r = c.get('/user/홍길동')
        self.assertNotIn('트렌드가 업데이트됐어요'.encode('utf-8'), r.data)

    def test_no_trends_file_does_not_show_banner_or_crash(self):
        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.db_path, 'TESTING': True})
        c = app.test_client()
        r = c.get('/user/홍길동')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('트렌드가 업데이트됐어요'.encode('utf-8'), r.data)


if __name__ == '__main__':
    unittest.main()
