"""고도화(2026-09-27) 자소서 트렌드 화면/트리거 배선 테스트. 실제 web_search API 호출은
전부 스텁으로 막는다 — 특히 TESTING 가드가 실제로 동작하는지가 핵심이다(안 그러면 이
프로젝트의 거의 모든 웹 테스트가 GET /user/<name> 을 거칠 때마다 실제 API 를 호출하게 된다).
"""
import json
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
        # S11-6: 화면이 "현재 쓰이는 내용" 으로 보여주는 것은 승인본이다.
        (trends_dir / 'approved.md').write_text(
            '# 자소서 작성 트렌드\n\n## 구조\n두괄식 선호', encoding='utf-8')
        r = self.c.get('/trends')
        # 고도화(2026-09-27)로 렌더링이 클라이언트 마크다운 파서로 옮겨가면서, 본문은
        # 리터럴 UTF-8 텍스트가 아니라 |tojson 으로 이스케이프된 JS 문자열 리터럴로
        # 응답에 들어간다 (예: "두괄식" → "두괄식"). 실제 페이지에서는
        # markdown.js 가 이를 파싱해 화면에 렌더링한다.
        body = r.data.decode('utf-8')
        expected = json.dumps('두괄식 선호')[1:-1]  # 앞뒤 큰따옴표 제외한 이스케이프 본문
        self.assertIn(expected, body)


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


class TrendsApprovalTest(unittest.TestCase):
    """S11-6 / 검토 F-1: 자동 수집본이 검토 없이 작성 정책이 되던 구조를 끊었는지."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        self.trends_dir = self.root / 'references' / 'trends'
        self.trends_dir.mkdir(parents=True)
        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.tmp / 'app.db', 'TESTING': True})
        self.c = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_raw(self, body='쉼표를 많이 쓰면 AI 티가 난다'):
        raw = self.trends_dir / 'raw'
        raw.mkdir(parents=True, exist_ok=True)
        (raw / '2026-09-27.md').write_text(f'> 최종 갱신일: 2026-09-27\n\n{body}', encoding='utf-8')

    def test_raw_alone_is_marked_pending_and_not_in_use(self):
        self.write_raw()
        r = self.c.get('/trends')
        self.assertEqual(r.status_code, 200)
        self.assertIn('검토 대기'.encode('utf-8'), r.data)
        self.assertIsNone(trends.load_trends(self.trends_dir))

    def test_approve_promotes_raw_to_approved(self):
        self.write_raw()
        r = self.c.post('/trends/approve', json={})
        self.assertEqual(r.status_code, 200)
        approved = trends.load_trends(self.trends_dir)
        self.assertIn('AI 티가 난다', approved)
        self.assertIn('승인일', approved)

    def test_approve_with_edited_content_wins(self):
        """항목을 추리거나 문구를 고쳐서 승인할 수 있어야 한다."""
        self.write_raw()
        self.c.post('/trends/approve', json={'content': '근거 있는 수치만 포함한다'})
        approved = trends.load_trends(self.trends_dir)
        self.assertIn('근거 있는 수치만', approved)
        self.assertNotIn('AI 티가 난다', approved)

    def test_approve_archives_previous_version(self):
        self.write_raw()
        self.c.post('/trends/approve', json={'content': '1차 승인본'})
        self.c.post('/trends/approve', json={'content': '2차 승인본'})
        history = list((self.trends_dir / 'history').glob('*.md'))
        self.assertEqual(len(history), 1)
        self.assertIn('1차 승인본', history[0].read_text(encoding='utf-8'))
        self.assertIn('2차 승인본', trends.load_trends(self.trends_dir))

    def test_approve_without_anything_to_approve_fails(self):
        r = self.c.post('/trends/approve', json={})
        self.assertEqual(r.status_code, 400)


class LegacyMigrationTest(unittest.TestCase):
    """S11-6: 예전 단일 파일은 raw/ 로 옮기되 승인본으로 올리지 않는다."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        self.trends_dir = self.root / 'references' / 'trends'
        self.trends_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_legacy_file_moves_to_raw_not_approved(self):
        (self.trends_dir / 'coverletter-trends.md').write_text(
            '> 최종 갱신일: 2026-09-27\n\n옛 수집본', encoding='utf-8')
        create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.tmp / 'app.db', 'TESTING': True})
        self.assertFalse((self.trends_dir / 'coverletter-trends.md').exists())
        self.assertTrue((self.trends_dir / 'raw' / '2026-09-27.md').exists())
        # 검토를 거친 적 없는 내용이므로 승인본이 되면 안 된다
        self.assertIsNone(trends.load_trends(self.trends_dir))
        self.assertTrue(trends.has_pending_review(self.trends_dir))

    def test_migration_is_idempotent(self):
        (self.trends_dir / 'coverletter-trends.md').write_text(
            '> 최종 갱신일: 2026-09-27\n\n옛 수집본', encoding='utf-8')
        for _ in range(3):
            create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.tmp / 'app.db', 'TESTING': True})
        self.assertEqual(len(list((self.trends_dir / 'raw').glob('*.md'))), 1)


if __name__ == '__main__':
    unittest.main()
