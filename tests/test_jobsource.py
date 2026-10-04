"""공고 원문 가져오기 (S11-16). Claude.md "외부 자료 수집 정책" 4단계의 코드 구현.

테스트는 네트워크를 타지 않는다 — opener 를 주입한다.
"""
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import jobsource as J  # noqa: E402
from web import create_app  # noqa: E402

LONG = '물류창고 운영팀 채용 공고 본문입니다. ' * 20   # MIN_TEXT_LENGTH 넘기기용


class PolicyTest(unittest.TestCase):
    """정책의 "금지" 구간은 네트워크를 타기 전에 걸러야 한다."""

    def test_tos_blocked_sites_are_refused_before_fetching(self):
        for url in ('https://www.jobkorea.co.kr/Recruit/GI_Read/1',
                    'https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=1',
                    'https://www.wanted.co.kr/wd/1'):
            r = J.fetch_url(url, opener=lambda u: '<p>' + LONG + '</p>')
            self.assertFalse(r.ok, url)
            self.assertEqual(r.reason, 'blocked')
            self.assertIn('붙여넣', r.hint)

    def test_subdomain_of_blocked_site_is_also_refused(self):
        r = J.check_policy('https://m.jobkorea.co.kr/Recruit/1')
        self.assertIsNotNone(r)
        self.assertEqual(r.reason, 'blocked')

    def test_allowed_site_passes_policy(self):
        self.assertIsNone(J.check_policy('https://jobs.kuehne-nagel.com/global/en'))

    def test_non_http_scheme_refused(self):
        self.assertEqual(J.check_policy('ftp://example.com/a').reason, 'invalid')
        self.assertEqual(J.check_policy('file:///etc/passwd').reason, 'invalid')

    def test_private_addresses_refused(self):
        """사용자가 준 주소라도 내부 서비스를 찌르게 둘 이유가 없다."""
        for url in ('http://localhost:8080/x', 'http://127.0.0.1/x'):
            r = J.check_policy(url)
            self.assertIsNotNone(r, url)
            self.assertEqual(r.reason, 'blocked')


class FetchTest(unittest.TestCase):
    def setUp(self):
        J._last_request_at.clear()

    def test_fetches_and_strips_html(self):
        html = f'<html><head><style>b{{}}</style></head><body><script>x=1</script>' \
               f'<h1>물류창고 운영팀</h1><p>{LONG}</p></body></html>'
        r = J.fetch_url('https://example.com/job/1', opener=lambda u: html, sleep=lambda s: None)
        self.assertTrue(r.ok)
        self.assertIn('물류창고 운영팀', r.text)
        self.assertNotIn('x=1', r.text)       # script 제거
        self.assertNotIn('<p>', r.text)
        self.assertTrue(r.fetched_at)         # 수집 시점 기록 (정책 요구사항)

    def test_javascript_rendered_page_is_reported_not_silently_empty(self):
        r = J.fetch_url('https://example.com/job/1',
                        opener=lambda u: '<html><body><div id="root"></div></body></html>',
                        sleep=lambda s: None)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, 'empty')
        self.assertIn('PDF', r.hint)

    def test_login_wall_gives_a_useful_hint(self):
        import urllib.error

        def boom(url):
            raise urllib.error.HTTPError(url, 403, 'Forbidden', {}, io.BytesIO(b''))

        r = J.fetch_url('https://example.com/job/1', opener=boom, sleep=lambda s: None)
        self.assertFalse(r.ok)
        self.assertIn('로그인', r.hint)

    def test_rate_limit_waits_between_requests_to_same_host(self):
        """정책의 "요청 간격 적용"."""
        slept = []
        for _ in range(2):
            J.fetch_url('https://example.com/a', opener=lambda u: f'<p>{LONG}</p>',
                        sleep=slept.append)
        self.assertEqual(len(slept), 1)
        self.assertGreater(slept[0], 0)


class ExtractFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_text_file(self):
        f = self.tmp / '공고.txt'
        f.write_text(LONG, encoding='utf-8')
        r = J.extract_file(f)
        self.assertTrue(r.ok)
        self.assertIn('물류창고', r.text)
        self.assertEqual(r.meta['filename'], '공고.txt')

    def test_unsupported_format_explains_instead_of_crashing(self):
        f = self.tmp / '공고.zip'
        f.write_bytes(b'PK\x03\x04')
        r = J.extract_file(f)
        self.assertFalse(r.ok)
        self.assertIn('붙여넣', r.hint)


class JobsRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        self.root.mkdir()
        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.tmp / 'app.db',
                           'TESTING': True})
        self.c = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fetch_route_refuses_blocked_site_with_reason(self):
        r = self.c.post('/jobs/fetch', json={'url': 'https://www.jobkorea.co.kr/Recruit/1'})
        self.assertEqual(r.status_code, 400)
        data = r.get_json()
        self.assertEqual(data['reason'], 'blocked')
        self.assertIn('잡코리아', data['error'])

    def test_fetch_route_returns_text_on_success(self):
        with patch.object(J, 'fetch_url',
                          return_value=J.FetchResult(ok=True, text=LONG, url='https://ex.com/1',
                                                      fetched_at='2026-10-04T10:00:00')):
            r = self.c.post('/jobs/fetch', json={'url': 'https://ex.com/1'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('물류창고', r.get_json()['raw_text'])

    def test_upload_route_reads_a_file(self):
        data = {'file': (io.BytesIO(LONG.encode('utf-8')), '공고.txt')}
        r = self.c.post('/jobs/upload', data=data, content_type='multipart/form-data')
        self.assertEqual(r.status_code, 200)
        self.assertIn('물류창고', r.get_json()['raw_text'])

    def test_upload_without_file_is_rejected(self):
        r = self.c.post('/jobs/upload', data={}, content_type='multipart/form-data')
        self.assertEqual(r.status_code, 400)

    def test_upload_does_not_keep_the_file(self):
        """업로드 파일은 임시 디렉터리에서만 다룬다. jobs/ 나 users/ 에 남기지 않는다."""
        data = {'file': (io.BytesIO(LONG.encode('utf-8')), '공고.txt')}
        self.c.post('/jobs/upload', data=data, content_type='multipart/form-data')
        self.assertEqual(list(self.root.rglob('공고.txt')), [])


if __name__ == '__main__':
    unittest.main()
