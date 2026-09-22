"""웹 라우트 구조와 경로 보안 회귀 테스트 (S0-2, S0-5).

실행 (프로젝트 루트에서):  python3 -m unittest discover -s tests -v
실제 users/, outputs/ 는 건드리지 않고 임시 디렉터리를 PROJECT_ROOT 로 주입한다.
"""
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web import create_app  # noqa: E402

EXPECTED_ROUTES = {
    ('/', 'GET'),
    ('/user/<name>', 'GET'),
    ('/new-user', 'POST'),
    ('/profile/<name>/setup', 'GET'),
    ('/profile/<name>/setup', 'POST'),
    ('/upload/<name>', 'POST'),
    ('/output/<name>/<filename>', 'GET'),
    ('/output/<name>/<filename>/save', 'POST'),
    ('/experience/<name>/<filename>', 'GET'),
    ('/experience/<name>/<filename>/save', 'POST'),
    ('/parse/<name>', 'POST'),
    ('/static/<path:filename>', 'GET'),
}


class WebTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        (self.root / 'users' / '홍길동' / 'experiences').mkdir(parents=True)
        (self.root / 'users' / '홍길동' / 'raw' / 'apply').mkdir(parents=True)
        (self.root / 'outputs' / '홍길동').mkdir(parents=True)
        (self.root / 'users' / '홍길동' / 'experiences' / 'a.md').write_text('# 경험A\n- 시기: 2024\n', encoding='utf-8')
        (self.root / 'outputs' / '홍길동' / 'x.md').write_text('본문', encoding='utf-8')
        (self.tmp / 'secret.md').write_text('SECRET', encoding='utf-8')   # root 밖 파일
        app = create_app({'PROJECT_ROOT': self.root, 'TESTING': True})
        self.app, self.c = app, app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class RouteStructureTest(WebTestBase):
    def test_route_table_unchanged(self):
        actual = {(r.rule, m) for r in self.app.url_map.iter_rules() for m in r.methods - {'HEAD', 'OPTIONS'}}
        self.assertEqual(actual, EXPECTED_ROUTES)

    def test_debug_off_by_default(self):
        self.assertFalse(self.app.debug)

    def test_pages_render(self):
        self.assertEqual(self.c.get('/').status_code, 200)
        self.assertEqual(self.c.get('/user/홍길동').status_code, 200)
        self.assertEqual(self.c.get('/profile/홍길동/setup').status_code, 200)
        self.assertEqual(self.c.get('/experience/홍길동/a.md').status_code, 200)
        self.assertEqual(self.c.get('/output/홍길동/x.md').status_code, 200)

    def test_missing_user_redirects_to_index(self):
        r = self.c.get('/profile/없는사람/setup')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers['Location'].endswith('/'))

    def test_profile_save_redirects_to_user_page(self):
        r = self.c.post('/profile/홍길동/setup', data={'이름': '홍길동', 'gpa': '4.0'})
        self.assertEqual(r.status_code, 302)
        self.assertIn('/user/', r.headers['Location'])
        self.assertIn('GPA: 4.0/4.5', (self.root / 'users' / '홍길동' / 'profile.md').read_text(encoding='utf-8'))

    def test_save_roundtrip(self):
        r = self.c.post('/experience/홍길동/a.md/save', json={'content': '수정됨'})
        self.assertEqual(r.get_json(), {'success': True})
        self.assertEqual((self.root / 'users' / '홍길동' / 'experiences' / 'a.md').read_text(encoding='utf-8'), '수정됨')


class PathTraversalTest(WebTestBase):
    def test_dotdot_segments_rejected(self):
        for path in ['/output/../secret.md', '/output/홍길동/..', '/experience/../x',
                     '/experience/홍길동/..', '/user/..', '/profile/../setup']:
            self.assertEqual(self.c.get(path).status_code, 400, path)

    def test_dotdot_not_served_when_slash_encoded(self):
        # 라우터가 %2f 를 경로 구분자로 보고 404 를 낸다. 400 이든 404 든 파일이 노출되면 안 된다.
        for path in ['/output/홍길동/..%2f..%2fsecret.md', '/experience/홍길동/..%2f..%2f..%2fsecret.md']:
            r = self.c.get(path)
            self.assertIn(r.status_code, (400, 404), path)
            self.assertNotIn(b'SECRET', r.data)

    def test_save_with_dotdot_rejected_and_nothing_written(self):
        for path in ['/output/../x/save', '/output/홍길동/../save', '/experience/../x/save']:
            r = self.c.post(path, json={'content': 'pwn'})
            self.assertEqual(r.status_code, 400, path)
        self.assertEqual((self.tmp / 'secret.md').read_text(encoding='utf-8'), 'SECRET')

    def test_new_user_rejects_bad_names_and_creates_nothing_outside(self):
        before = {p.name for p in self.tmp.iterdir()}
        for bad in ['../evil', '..', '.', 'a/b', 'a\\b', 'x' * 101]:
            r = self.c.post('/new-user', data={'name': bad})
            self.assertFalse(r.get_json()['success'], bad)
        self.assertEqual({p.name for p in self.tmp.iterdir()}, before)
        self.assertFalse((self.root / 'evil').exists())

    def test_new_user_creates_folders(self):
        r = self.c.post('/new-user', data={'name': '김새이름'})
        self.assertTrue(r.get_json()['success'])
        for sub in ('raw/apply', 'raw/logs', 'experiences'):
            self.assertTrue((self.root / 'users' / '김새이름' / sub).is_dir(), sub)
        self.assertTrue((self.root / 'outputs' / '김새이름').is_dir())


class UploadTest(WebTestBase):
    def upload(self, filename, kind='apply', user='홍길동'):
        return self.c.post(f'/upload/{user}', data={'kind': kind, 'files': (io.BytesIO(b'hi'), filename)},
                           content_type='multipart/form-data')

    def saved(self):
        return sorted(p.name for p in (self.root / 'users' / '홍길동' / 'raw' / 'apply').iterdir())

    def test_korean_filename_kept(self):
        self.assertEqual(self.upload('자소서_최종.pdf').get_json()['saved'], ['자소서_최종.pdf'])

    def test_directory_components_stripped(self):
        self.assertEqual(self.upload('../../evil.md').get_json()['saved'], ['evil.md'])
        self.assertEqual(self.upload('..\\..\\evil.txt').get_json()['saved'], ['evil.txt'])
        self.assertFalse((self.tmp / 'evil.md').exists())
        self.assertFalse((self.root / 'evil.md').exists())

    def test_dangerous_chars_and_leading_dot_removed(self):
        self.assertEqual(self.upload('a:b?.docx').get_json()['saved'], ['ab.docx'])
        self.assertEqual(self.upload('.hidden.md').get_json()['saved'], ['hidden.md'])

    def test_extension_checked_case_insensitively(self):
        self.assertEqual(self.upload('한글.HWPX').get_json()['saved'], ['한글.HWPX'])
        r = self.upload('run.sh').get_json()
        self.assertEqual((r['saved'], r['skipped']), ([], ['run.sh']))
        self.assertNotIn('run.sh', self.saved())

    def test_invalid_kind_and_user_rejected(self):
        self.assertEqual(self.upload('a.md', kind='../../x').status_code, 400)
        self.assertEqual(self.upload('a.md', user='..').status_code, 400)


if __name__ == '__main__':
    unittest.main()
