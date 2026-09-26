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
    ('/output/<name>/<path:filename>', 'GET'),
    ('/output/<name>/<path:filename>/save', 'POST'),
    ('/experience/<name>/<filename>', 'GET'),
    ('/experience/<name>/<filename>/save', 'POST'),
    ('/parse/<name>', 'POST'),
    ('/generate/<name>', 'POST'),
    ('/generate/<name>', 'GET'),
    ('/generate/<name>/bundle', 'POST'),
    ('/raw-fallback/<name>', 'POST'),
    ('/elicit/<name>', 'GET'),
    ('/elicit/<name>', 'POST'),
    ('/jobs', 'GET'),
    ('/jobs/preview', 'POST'),
    ('/jobs/save', 'POST'),
    ('/matches/<name>', 'GET'),
    ('/matches/<name>/run', 'POST'),
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
        (self.root / 'outputs' / '홍길동' / '테스트기업_직무').mkdir(parents=True)  # S7 서브폴더 구조
        (self.root / 'outputs' / '홍길동' / '테스트기업_직무' / '자소서.md').write_text('서브폴더 본문', encoding='utf-8')
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

    def test_output_subfolder_path_served(self):
        """S7 부터 outputs/[이름]/[기업]_[직무]/*.md 서브폴더가 생겼다 — /output 라우트가
        <path:filename> 으로 슬래시 있는 경로도 서빙해야 한다."""
        r = self.c.get('/output/홍길동/테스트기업_직무/자소서.md')
        self.assertEqual(r.status_code, 200)
        self.assertIn('서브폴더 본문'.encode('utf-8'), r.data)

    def test_output_subfolder_save_roundtrip(self):
        r = self.c.post('/output/홍길동/테스트기업_직무/자소서.md/save', json={'content': '수정됨'})
        self.assertEqual(r.get_json(), {'success': True})
        self.assertEqual(
            (self.root / 'outputs' / '홍길동' / '테스트기업_직무' / '자소서.md').read_text(encoding='utf-8'),
            '수정됨')

    def test_output_subfolder_traversal_rejected(self):
        for path in ['/output/홍길동/테스트기업_직무/../../secret.md',
                     '/output/홍길동/../secret.md',
                     '/output/홍길동/테스트기업_직무/..']:
            self.assertEqual(self.c.get(path).status_code, 400, path)
        self.assertEqual((self.tmp / 'secret.md').read_text(encoding='utf-8'), 'SECRET')

    def test_missing_user_redirects_to_index(self):
        r = self.c.get('/profile/없는사람/setup')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers['Location'].endswith('/'))

    def test_profile_save_redirects_to_user_page(self):
        r = self.c.post('/profile/홍길동/setup', data={'이름': '홍길동', 'gpa': '4.0'})
        self.assertEqual(r.status_code, 302)
        self.assertIn('/user/', r.headers['Location'])
        self.assertIn('GPA: 4.0/4.5', (self.root / 'users' / '홍길동' / 'profile.md').read_text(encoding='utf-8'))

    def test_profile_save_writes_writing_rules(self):
        """S3-5: 작성 규칙 탭에 입력한 내용이 writing-rules.md 에 실제로 반영된다."""
        r = self.c.post('/profile/홍길동/setup', data={
            '이름': '홍길동', '금지표현': '"열정", "도전" 금지', '선호표현': '수치를 구체적으로 명시',
        })
        self.assertEqual(r.status_code, 302)
        rules = (self.root / 'users' / '홍길동' / 'writing-rules.md').read_text(encoding='utf-8')
        self.assertIn('"열정", "도전" 금지', rules)
        self.assertIn('수치를 구체적으로 명시', rules)

    def test_setup_page_prefills_existing_writing_rules(self):
        (self.root / 'users' / '홍길동' / 'writing-rules.md').write_text(
            '# 홍길동 작성 규칙\n\n## 구조\n\n두괄식\n\n## 금지 표현\n\n클리셰 금지\n\n'
            '## 선호 표현\n\n수치 명시\n\n## 기타\n\n', encoding='utf-8')
        r = self.c.get('/profile/홍길동/setup')
        self.assertIn('클리셰 금지'.encode('utf-8'), r.data)

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


class RawFallbackTest(WebTestBase):
    """S2-2: 추출 실패 파일의 내용을 직접 붙여넣는 폴백. 원본 raw 파일은 절대 건드리지 않는다."""

    def setUp(self):
        super().setUp()
        self.original = self.root / 'users' / '홍길동' / 'raw' / 'apply' / '스캔본.hwp'
        self.original.write_bytes(b'\x00binary garbage that fails to extract')

    def post(self, **kw):
        payload = {'kind': 'apply', 'filename': '스캔본.hwp', 'content': '붙여넣은 실제 내용입니다. ' * 3}
        payload.update(kw)
        return self.c.post('/raw-fallback/홍길동', json=payload)

    def test_saves_as_sibling_file_without_touching_original(self):
        r = self.post()
        data = r.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['saved_as'], '스캔본.붙여넣기.md')

        sibling = self.original.parent / '스캔본.붙여넣기.md'
        self.assertTrue(sibling.exists())
        self.assertIn('붙여넣은 실제 내용입니다', sibling.read_text(encoding='utf-8'))
        # 원본은 바이트 하나도 바뀌지 않아야 한다
        self.assertEqual(self.original.read_bytes(), b'\x00binary garbage that fails to extract')

    def test_too_short_content_rejected(self):
        r = self.post(content='짧음')
        data = r.get_json()
        self.assertFalse(data['success'])
        self.assertFalse((self.original.parent / '스캔본.붙여넣기.md').exists())

    def test_missing_original_file_rejected(self):
        r = self.post(filename='없는파일.hwp')
        self.assertFalse(r.get_json()['success'])

    def test_invalid_kind_rejected(self):
        self.assertEqual(self.post(kind='../../x').status_code, 400)

    def test_path_traversal_in_filename_rejected(self):
        self.assertEqual(self.post(filename='../../../etc/passwd').status_code, 400)
        self.assertFalse((self.tmp / 'etc' / 'passwd.붙여넣기.md').exists())

    def test_resubmission_overwrites_previous_paste(self):
        self.post(content='첫 번째 붙여넣기 내용입니다. ' * 3)
        self.post(content='두 번째로 고친 붙여넣기 내용입니다. ' * 3)
        sibling = self.original.parent / '스캔본.붙여넣기.md'
        self.assertIn('두 번째로 고친', sibling.read_text(encoding='utf-8'))
        self.assertNotIn('첫 번째', sibling.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
