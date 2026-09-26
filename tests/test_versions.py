"""S8-1 버전 보존 테스트. LLM 을 안 쓰는 순수 파일/DB 로직이라 전부 실제 동작을 검증한다."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import db, versions  # noqa: E402


class InferKindTest(unittest.TestCase):
    def test_known_stems(self):
        self.assertEqual(versions.infer_kind('이력서.md'), 'resume')
        self.assertEqual(versions.infer_kind('자소서.md'), 'coverletter')
        self.assertEqual(versions.infer_kind('portfolio-outline.md'), 'portfolio')

    def test_unknown_stem_defaults_to_coverletter(self):
        self.assertEqual(versions.infer_kind('쿠팡_물류 오퍼레이션기획.md'), 'coverletter')


class NextVersionNumberTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_dir_starts_at_1(self):
        self.assertEqual(versions.next_version_number(self.tmp / 'nope'), 1)

    def test_increments_from_existing(self):
        vdir = self.tmp / 'v'
        vdir.mkdir()
        (vdir / 'v1.md').write_text('x', encoding='utf-8')
        (vdir / 'v2.md').write_text('x', encoding='utf-8')
        self.assertEqual(versions.next_version_number(vdir), 3)

    def test_ignores_unrelated_files(self):
        vdir = self.tmp / 'v'
        vdir.mkdir()
        (vdir / 'v1.md').write_text('x', encoding='utf-8')
        (vdir / 'notes.txt').write_text('x', encoding='utf-8')
        (vdir / 'vX.md').write_text('x', encoding='utf-8')
        self.assertEqual(versions.next_version_number(vdir), 2)


class SaveAndListVersionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / 'app.db'
        self.output_dir = self.tmp / 'outputs' / '홍길동'
        self.output_dir.mkdir(parents=True)
        db.init_db(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_three_saves_produce_v1_v2_v3_all_readable(self):
        """S8-1 완료 조건 그대로: 저장 3회 후 v1, v2, v3 모두 조회 가능해야 한다."""
        r1 = versions.save_version(self.output_dir, '기업_직무/자소서.md', '첫 번째 내용', '홍길동', db_path=self.db_path)
        r2 = versions.save_version(self.output_dir, '기업_직무/자소서.md', '두 번째 내용', '홍길동', db_path=self.db_path)
        r3 = versions.save_version(self.output_dir, '기업_직무/자소서.md', '세 번째 내용', '홍길동', db_path=self.db_path)

        self.assertEqual([r1.version, r2.version, r3.version], [1, 2, 3])

        v1 = self.output_dir / '기업_직무' / '자소서' / 'v1.md'
        v2 = self.output_dir / '기업_직무' / '자소서' / 'v2.md'
        v3 = self.output_dir / '기업_직무' / '자소서' / 'v3.md'
        self.assertEqual(v1.read_text(encoding='utf-8'), '첫 번째 내용')
        self.assertEqual(v2.read_text(encoding='utf-8'), '두 번째 내용')
        self.assertEqual(v3.read_text(encoding='utf-8'), '세 번째 내용')

    def test_current_file_always_matches_latest_version(self):
        versions.save_version(self.output_dir, '기업_직무/자소서.md', '옛날 내용', '홍길동', db_path=self.db_path)
        versions.save_version(self.output_dir, '기업_직무/자소서.md', '최신 내용', '홍길동', db_path=self.db_path)
        current = self.output_dir / '기업_직무' / '자소서.md'
        self.assertEqual(current.read_text(encoding='utf-8'), '최신 내용')

    def test_documents_table_records_each_save(self):
        versions.save_version(self.output_dir, '기업_직무/이력서.md', 'a' * 50, '홍길동', db_path=self.db_path)
        versions.save_version(self.output_dir, '기업_직무/이력서.md', 'a' * 80, '홍길동', db_path=self.db_path)

        conn = db.get_conn(self.db_path)
        rows = conn.execute('SELECT * FROM documents ORDER BY version').fetchall()
        conn.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['kind'], 'resume')
        self.assertEqual(rows[0]['char_count'], 50)
        self.assertEqual(rows[1]['char_count'], 80)
        self.assertEqual(rows[0]['user'], '홍길동')

    def test_list_versions_returns_ordered_history(self):
        for content in ['v1 내용', 'v2 내용', 'v3 내용']:
            versions.save_version(self.output_dir, '기업_직무/자소서.md', content, '홍길동', db_path=self.db_path)
        history = versions.list_versions(self.output_dir, '기업_직무/자소서.md', '홍길동', db_path=self.db_path)
        self.assertEqual([h.version for h in history], [1, 2, 3])
        self.assertEqual(history[0].path, '기업_직무/자소서/v1.md')

    def test_different_documents_do_not_mix_version_history(self):
        versions.save_version(self.output_dir, '기업_직무/이력서.md', '이력서 v1', '홍길동', db_path=self.db_path)
        versions.save_version(self.output_dir, '기업_직무/자소서.md', '자소서 v1', '홍길동', db_path=self.db_path)
        versions.save_version(self.output_dir, '기업_직무/이력서.md', '이력서 v2', '홍길동', db_path=self.db_path)

        resume_history = versions.list_versions(self.output_dir, '기업_직무/이력서.md', '홍길동', db_path=self.db_path)
        cover_history = versions.list_versions(self.output_dir, '기업_직무/자소서.md', '홍길동', db_path=self.db_path)
        self.assertEqual(len(resume_history), 2)
        self.assertEqual(len(cover_history), 1)

    def test_different_users_same_folder_name_do_not_mix(self):
        other_output_dir = self.tmp / 'outputs' / '다른사람'
        other_output_dir.mkdir(parents=True)
        versions.save_version(self.output_dir, '기업_직무/자소서.md', '홍길동 것', '홍길동', db_path=self.db_path)
        versions.save_version(other_output_dir, '기업_직무/자소서.md', '다른사람 것', '다른사람', db_path=self.db_path)

        hong_history = versions.list_versions(self.output_dir, '기업_직무/자소서.md', '홍길동', db_path=self.db_path)
        other_history = versions.list_versions(other_output_dir, '기업_직무/자소서.md', '다른사람', db_path=self.db_path)
        self.assertEqual(len(hong_history), 1)
        self.assertEqual(len(other_history), 1)


if __name__ == '__main__':
    unittest.main()
