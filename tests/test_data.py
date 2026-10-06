"""web/data.py 테스트. 고도화(2026-09-29): 경험 목록을 '시작한 시기순(오래된 순)'으로
정렬하도록 바꿨다 — skills/query-experiences.md 의 타임라인 규칙과 대시보드 목록을 맞춘 것."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web import create_app  # noqa: E402
from web.data import _start_date_key, get_experiences, get_outputs  # noqa: E402


def write_experience(exp_dir, filename, title, date):
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / filename).write_text(
        f'# {title}\n\n## 메타\n\n- 시기: {date}\n\n## 활동 내용\n\n내용', encoding='utf-8')


class StartDateKeyTest(unittest.TestCase):
    def test_year_month_range(self):
        self.assertEqual(_start_date_key('2024.08 ~ 2026.08'), (2024, 8, 1))

    def test_year_month_day(self):
        self.assertEqual(_start_date_key('2026.01.12 ~ 2026.02.27 (7주)'), (2026, 1, 12))

    def test_year_only_defaults_to_january(self):
        self.assertEqual(_start_date_key('2024 (정확한 월 확인 필요, 춘계)'), (2024, 1, 1))

    def test_unparseable_sorts_last(self):
        self.assertEqual(_start_date_key('-'), (9999, 0, 0))

    def test_year_month_before_year_only_same_year(self):
        # "2024" 만 있는 것보다 "2024.03" 이 나중이어야 자연스럽다(월 정보가 없으면 1월로 간주) —
        # 실제로는 정확한 월을 모르는 경우가 섞여 있어 완벽한 순서를 보장하진 않지만, 최소한
        # 연도만 다른 경우는 확실히 오래된 순으로 나와야 한다.
        self.assertLess(_start_date_key('2023.10'), _start_date_key('2024.03'))


class GetExperiencesOrderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        self.exp_dir = self.root / 'users' / '홍길동' / 'experiences'
        app = create_app({'PROJECT_ROOT': self.root, 'TESTING': True})
        self.app = app

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sorted_by_start_date_not_filename(self):
        # 파일명 알파벳 순서로는 나중이지만(z...) 시작 시기가 더 이른 경험이 먼저 나와야 한다.
        write_experience(self.exp_dir, 'z-최근파일명.md', '최근 활동', '2026.01 ~ 2026.03')
        write_experience(self.exp_dir, 'a-오래된파일명.md', '오래된 활동', '2020.05 ~ 2020.08')
        with self.app.app_context():
            names = [e['name'] for e in get_experiences('홍길동')]
        self.assertEqual(names, ['오래된 활동', '최근 활동'])

    def test_no_experiences_returns_empty_list(self):
        with self.app.app_context():
            self.assertEqual(get_experiences('홍길동'), [])


class GetOutputsCaseTest(unittest.TestCase):
    """자소서 결과물 목록은 지원 건 단위다 (2026-10-06 사용자 요청).

    예전엔 outputs/[이름]/ 아래 .md 를 전부 평평하게 나열해서, 한 지원 건이 자소서·근거표·
    이력서·포트폴리오까지 네댓 줄로 흩어졌다.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        self.out = self.root / 'outputs' / '홍길동'
        self.out.mkdir(parents=True)
        self.app = create_app({'PROJECT_ROOT': self.root, 'TESTING': True})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _case(self, folder, files):
        d = self.out / folder
        d.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (d / name).write_text(text, encoding='utf-8')
        return d

    def outputs(self):
        with self.app.app_context():
            return get_outputs('홍길동')

    def test_one_row_per_case_folder(self):
        self._case('가기업_물류', {'자소서.md': '초안', '근거표.md': '근거', '이력서.md': '이력'})
        rows = self.outputs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], '가기업 · 물류')
        self.assertTrue(rows[0]['has_draft'])
        self.assertFalse(rows[0]['has_final'])
        self.assertEqual(rows[0]['others'], 2)

    def test_final_is_flagged(self):
        self._case('가기업_물류', {'자소서.md': '초안', '최종본.md': '최종'})
        self.assertTrue(self.outputs()[0]['has_final'])

    def test_version_folder_is_not_a_case(self):
        """lib/versions.py 는 '옛파일.md' 옆에 '옛파일/v1.md' 를 만든다. 이걸 건으로 세면
        같은 문서가 목록에 두 번 뜬다."""
        (self.out / '옛기업_직무.md').write_text('옛 평평한 구조', encoding='utf-8')
        vdir = self.out / '옛기업_직무'
        vdir.mkdir()
        (vdir / 'v1.md').write_text('버전1', encoding='utf-8')

        rows = self.outputs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['folder'], '')
        self.assertTrue(rows[0]['url'].startswith('/output/'))

    def test_case_without_coverletter_still_listed(self):
        """이력서만 만든 폴더도 빈칸으로 두지 않고 그 파일을 초안 자리에 보여준다."""
        self._case('나기업_기획', {'이력서.md': '이력'})
        rows = self.outputs()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['has_draft'])
        self.assertEqual(rows[0]['others'], 0)

    def test_empty_outputs_dir(self):
        self.assertEqual(self.outputs(), [])
