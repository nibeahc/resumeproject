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

from lib import db  # noqa: E402
from web import create_app  # noqa: E402

EXPECTED_ROUTES = {
    ('/', 'GET'),
    ('/user/<name>', 'GET'),
    ('/new-user', 'POST'),
    ('/delete-user/<name>', 'POST'),
    ('/profile/<name>/setup', 'GET'),
    ('/profile/<name>/setup', 'POST'),
    ('/upload/<name>', 'POST'),
    ('/output/<name>/<path:filename>', 'GET'),
    ('/output/<name>/<path:filename>/save', 'POST'),
    ('/output/<name>/<path:filename>/versions', 'GET'),
    ('/output/<name>/<path:filename>/diff', 'GET'),
    ('/output/<name>/<path:filename>/docx', 'GET'),
    ('/experience/<name>/<filename>', 'GET'),
    ('/experience/<name>/<filename>/save', 'POST'),
    ('/experience/<name>/<filename>/delete', 'POST'),
    ('/experience/<name>/new', 'POST'),
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
    ('/applications/<name>', 'GET'),
    ('/applications/<name>/<int:job_id>/status', 'POST'),
    ('/applications/<name>/<int:job_id>/history', 'GET'),
    ('/trends', 'GET'),
    ('/trends/approve', 'POST'),
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
        # S8-1 부터 /output 저장이 documents 테이블(버전 기록)을 쓴다 — DB_PATH 를 임시 경로로
        # 안 주면 이 클래스를 상속하는 모든 테스트가 실제 프로젝트 data/app.db 를 건드리게 된다.
        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.tmp / 'app.db', 'TESTING': True})
        self.app, self.c = app, app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class RouteStructureTest(WebTestBase):
    def test_route_table_unchanged(self):
        actual = {(r.rule, m) for r in self.app.url_map.iter_rules() for m in r.methods - {'HEAD', 'OPTIONS'}}
        self.assertEqual(actual, EXPECTED_ROUTES)

    def test_user_page_shows_dashboard_summary(self):
        """S9-2: 메인(개인) 대시보드에 지원 현황이 한눈에 보여야 한다."""
        r_empty = self.c.get('/user/홍길동')
        self.assertIn('지원 현황'.encode('utf-8'), r_empty.data)

        conn = db.get_conn(self.tmp / 'app.db')
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json) "
            "VALUES ('manual','0001','대시보드기업','백엔드','[]','[]')")
        job_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        conn.commit()
        conn.close()
        self.c.post(f'/applications/홍길동/{job_id}/status', json={'status': '준비중'})

        r = self.c.get('/user/홍길동')
        body = r.get_data(as_text=True)
        self.assertIn('대시보드기업', body)

    def test_debug_off_by_default(self):
        self.assertFalse(self.app.debug)

    def test_pages_render(self):
        self.assertEqual(self.c.get('/').status_code, 200)
        self.assertEqual(self.c.get('/user/홍길동').status_code, 200)
        self.assertEqual(self.c.get('/profile/홍길동/setup').status_code, 200)
        self.assertEqual(self.c.get('/experience/홍길동/a.md').status_code, 200)
        self.assertEqual(self.c.get('/output/홍길동/x.md').status_code, 200)

    def test_elicit_link_visible_even_when_experiences_already_exist(self):
        """회귀 방지: /elicit 링크가 예전엔 경험이 0개일 때만 보여서, 경험이 하나라도 있으면
        화면에서 대화로 경험을 더 추가할 방법이 사라졌었다(사용자가 실제로 겪고 지적함)."""
        r = self.c.get('/user/홍길동')  # WebTestBase 의 홍길동은 이미 경험 a.md 를 갖고 있다
        body = r.get_data(as_text=True)
        self.assertIn(f'/elicit/홍길동', body)

    def test_output_subfolder_path_served(self):
        """S7 부터 outputs/[이름]/[기업]_[직무]/*.md 서브폴더가 생겼다 — /output 라우트가
        <path:filename> 으로 슬래시 있는 경로도 서빙해야 한다."""
        r = self.c.get('/output/홍길동/테스트기업_직무/자소서.md')
        self.assertEqual(r.status_code, 200)
        self.assertIn('서브폴더 본문'.encode('utf-8'), r.data)

    def test_output_subfolder_save_roundtrip(self):
        # S8-1: 저장은 버전을 남긴다(version 필드) — 이 fixture 파일은 setUp 에서 이미 한 번
        # 만들어졌으니 이 저장이 v1(그 기존 내용의 스냅샷은 아직 없다는 점에 주의: 버전은
        # save_version() 을 거친 저장부터 세므로 최초 저장이 v1이 된다).
        r = self.c.post('/output/홍길동/테스트기업_직무/자소서.md/save', json={'content': '수정됨'})
        self.assertEqual(r.get_json(), {'success': True, 'version': 1})
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


class ExperienceDeleteAndCreateTest(WebTestBase):
    """경험 삭제 + 빈 템플릿 추가(2026-09-27, 사용자 요청)."""

    def test_deleting_experience_moves_it_to_trash(self):
        r = self.c.post('/experience/홍길동/a.md/delete')
        self.assertTrue(r.get_json()['success'])
        self.assertFalse((self.root / 'users' / '홍길동' / 'experiences' / 'a.md').exists())
        trashed = list((self.root / '_trash').glob('홍길동__experience__*'))
        self.assertEqual(len(trashed), 1)
        self.assertTrue((trashed[0] / 'a.md').exists())

    def test_deleting_missing_experience_returns_404(self):
        r = self.c.post('/experience/홍길동/없음.md/delete')
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.get_json()['success'])

    def test_delete_experience_path_traversal_rejected(self):
        r = self.c.post('/experience/../secret.md/delete')
        self.assertEqual(r.status_code, 400)
        self.assertEqual((self.tmp / 'secret.md').read_text(encoding='utf-8'), 'SECRET')

    def test_creating_blank_experience_uses_template(self):
        r = self.c.post('/experience/홍길동/new', data={'title': '학회 발표'})
        data = r.get_json()
        self.assertTrue(data['success'])
        saved = self.root / 'users' / '홍길동' / 'experiences' / data['filename']
        self.assertTrue(saved.exists())
        text = saved.read_text(encoding='utf-8')
        self.assertIn('# 학회 발표', text)
        self.assertIn('## STAR 품질 체크', text)  # 템플릿 그대로 들어갔는지

    def test_creating_blank_experience_without_title_rejected(self):
        r = self.c.post('/experience/홍길동/new', data={'title': ''})
        self.assertFalse(r.get_json()['success'])

    def test_creating_blank_experience_twice_does_not_overwrite(self):
        r1 = self.c.post('/experience/홍길동/new', data={'title': '학회 발표'})
        r2 = self.c.post('/experience/홍길동/new', data={'title': '학회 발표'})
        self.assertNotEqual(r1.get_json()['filename'], r2.get_json()['filename'])
        exp_dir = self.root / 'users' / '홍길동' / 'experiences'
        self.assertTrue((exp_dir / r1.get_json()['filename']).exists())
        self.assertTrue((exp_dir / r2.get_json()['filename']).exists())


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

    def test_delete_user_dotdot_rejected(self):
        r = self.c.post('/delete-user/..')
        self.assertEqual(r.status_code, 400)
        self.assertTrue((self.root / 'users' / '홍길동').exists())


class DeleteUserTest(WebTestBase):
    """홈 화면 "사용자 삭제"(2026-09-27, 사용자 요청): 삭제 버튼 → 확인창 → 최종 삭제.
    실제로는 rm 이 아니라 _trash/ 로 옮긴다(실수 클릭 대비 안전장치)."""

    def test_deleting_user_removes_from_list_and_moves_to_trash(self):
        r = self.c.post('/delete-user/홍길동')
        self.assertTrue(r.get_json()['success'])
        self.assertFalse((self.root / 'users' / '홍길동').exists())
        self.assertFalse((self.root / 'outputs' / '홍길동').exists())
        # 실제로 지워진 게 아니라 _trash/ 아래로 옮겨져 있어야 한다 (복구 가능)
        trashed = list((self.root / '_trash').glob('홍길동__*'))
        self.assertEqual(len(trashed), 1)
        self.assertTrue((trashed[0] / 'users' / 'experiences' / 'a.md').exists())
        self.assertTrue((trashed[0] / 'outputs' / 'x.md').exists())
        # 홈 화면 목록에서도 사라져야 한다
        idx = self.c.get('/').data.decode('utf-8')
        self.assertNotIn('홍길동', idx)

    def test_deleting_nonexistent_user_returns_error(self):
        r = self.c.post('/delete-user/없는사람')
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.get_json()['success'])

    def test_deleting_user_clears_related_db_rows(self):
        db.init_db(self.tmp / 'app.db')
        conn = db.get_conn(self.tmp / 'app.db')
        conn.execute("INSERT INTO jobs (source, company, position) VALUES ('paste', 'A사', '백엔드')")
        job_id = conn.execute('SELECT id FROM jobs').fetchone()['id']
        conn.execute("INSERT INTO matches (user, job_id, score) VALUES ('홍길동', ?, 80)", (job_id,))
        conn.execute("INSERT INTO applications (user, job_id, status) VALUES ('홍길동', ?, '관심')", (job_id,))
        conn.commit()
        app_id = conn.execute('SELECT id FROM applications').fetchone()['id']
        conn.execute("INSERT INTO application_events (application_id, to_status) VALUES (?, '관심')", (app_id,))
        conn.commit()
        conn.close()

        r = self.c.post('/delete-user/홍길동')
        self.assertTrue(r.get_json()['success'])

        conn = db.get_conn(self.tmp / 'app.db')
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM matches WHERE user='홍길동'").fetchone()['c'], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM applications WHERE user='홍길동'").fetchone()['c'], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM application_events").fetchone()['c'], 0)
            # 공용 데이터인 jobs 는 남아있어야 한다(사용자 개인 데이터가 아니므로)
            self.assertEqual(conn.execute('SELECT COUNT(*) c FROM jobs').fetchone()['c'], 1)
        finally:
            conn.close()


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


class VersioningRouteTest(WebTestBase):
    """S8-1: /output 저장이 덮어쓰지 않고 버전을 남기는지, 조회/비교가 되는지."""

    def save(self, content, doc='테스트기업_직무/자소서.md'):
        return self.c.post(f'/output/홍길동/{doc}/save', json={'content': content})

    def test_three_saves_produce_v1_v2_v3_all_viewable(self):
        """ROADMAP S8-1 완료 조건 그대로: 저장 3회 후 v1, v2, v3 모두 조회 가능."""
        r1 = self.save('첫 번째 버전')
        r2 = self.save('두 번째 버전')
        r3 = self.save('세 번째 버전')
        self.assertEqual([r1.get_json()['version'], r2.get_json()['version'], r3.get_json()['version']], [1, 2, 3])

        for n, expected in [(1, '첫 번째 버전'), (2, '두 번째 버전'), (3, '세 번째 버전')]:
            r = self.c.get(f'/output/홍길동/테스트기업_직무/자소서/v{n}.md')
            self.assertEqual(r.status_code, 200, f'v{n} 조회 실패')
            self.assertIn(expected.encode('utf-8'), r.data)

    def test_versions_endpoint_lists_history(self):
        self.save('v1')
        self.save('v2')
        r = self.c.get('/output/홍길동/테스트기업_직무/자소서.md/versions')
        data = r.get_json()
        self.assertEqual([v['version'] for v in data['versions']], [1, 2])
        self.assertEqual(data['versions'][0]['char_count'], 2)

    def test_diff_shows_line_changes_between_versions(self):
        self.save('첫 줄\n둘째 줄')
        self.save('첫 줄\n바뀐 둘째 줄')
        r = self.c.get('/output/홍길동/테스트기업_직무/자소서.md/diff?a=1&b=2')
        data = r.get_json()
        self.assertTrue(data['success'])
        joined = '\n'.join(data['diff'])
        self.assertIn('-둘째 줄', joined)
        self.assertIn('+바뀐 둘째 줄', joined)

    def test_diff_missing_version_404(self):
        self.save('v1')
        r = self.c.get('/output/홍길동/테스트기업_직무/자소서.md/diff?a=1&b=9')
        self.assertEqual(r.status_code, 404)

    def test_versions_route_rejects_traversal(self):
        for path in ['/output/홍길동/../secret.md/versions',
                     '/output/홍길동/테스트기업_직무/../../secret.md/versions']:
            self.assertEqual(self.c.get(path).status_code, 400, path)

    def test_diff_route_rejects_traversal_and_bad_version(self):
        self.assertEqual(
            self.c.get('/output/홍길동/../secret.md/diff?a=1&b=2').status_code, 400)
        self.assertEqual(
            self.c.get('/output/홍길동/테스트기업_직무/자소서.md/diff?a=x&b=2').status_code, 400)

    def test_docx_download_returns_valid_word_file(self):
        """S8-2: /docx 가 실제로 열리는 .docx 를 돌려주는지 (한글 폰트 깨짐은 사람이 열어봐야
        아는 항목이지만, 최소한 파일 자체가 유효하고 내용이 들어있는지는 코드로 검증한다)."""
        import io
        from docx import Document
        r = self.c.get('/output/홍길동/테스트기업_직무/자소서.md/docx')
        self.assertEqual(r.status_code, 200)
        self.assertIn('wordprocessingml', r.headers['Content-Type'])
        doc = Document(io.BytesIO(r.data))
        self.assertIn('서브폴더 본문', [p.text for p in doc.paragraphs])

    def test_docx_download_missing_file_404(self):
        r = self.c.get('/output/홍길동/없는파일.md/docx')
        self.assertEqual(r.status_code, 404)

    def test_docx_download_rejects_traversal(self):
        self.assertEqual(self.c.get('/output/홍길동/../secret.md/docx').status_code, 400)

    def test_editor_shows_docx_link_for_output_only(self):
        r_output = self.c.get('/output/홍길동/x.md')
        self.assertIn(b'DOCX', r_output.data)
        r_experience = self.c.get('/experience/홍길동/a.md')
        self.assertNotIn(b'DOCX', r_experience.data)

    def test_editor_shows_version_history_for_output_only(self):
        r_output = self.c.get('/output/홍길동/x.md')
        self.assertIn(b'versionCard', r_output.data)
        r_experience = self.c.get('/experience/홍길동/a.md')
        self.assertNotIn(b'versionCard', r_experience.data)

    def test_resave_does_not_touch_other_documents_history(self):
        self.save('이력서 버전', doc='테스트기업_직무/이력서.md')
        self.save('자소서 버전1', doc='테스트기업_직무/자소서.md')
        self.save('자소서 버전2', doc='테스트기업_직무/자소서.md')

        resume_versions = self.c.get('/output/홍길동/테스트기업_직무/이력서.md/versions').get_json()['versions']
        cover_versions = self.c.get('/output/홍길동/테스트기업_직무/자소서.md/versions').get_json()['versions']
        self.assertEqual(len(resume_versions), 1)
        self.assertEqual(len(cover_versions), 2)


if __name__ == '__main__':
    unittest.main()
