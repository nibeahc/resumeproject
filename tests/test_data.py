"""web/data.py 테스트. 고도화(2026-09-29): 경험 목록을 '시작한 시기순(오래된 순)'으로
정렬하도록 바꿨다 — skills/query-experiences.md 의 타임라인 규칙과 대시보드 목록을 맞춘 것."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web import create_app  # noqa: E402
from web.data import (MARK_CLOSE, MARK_OPEN, _start_date_key, build_case,  # noqa: E402
                      get_experiences, get_outputs, mark_changes)


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


class CaseDirTestBase(unittest.TestCase):
    """임시 outputs/홍길동/ 아래에 지원 건 폴더를 만든다."""

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


class GetOutputsCaseTest(CaseDirTestBase):
    """자소서 결과물 목록은 지원 건 단위다 (2026-10-06 사용자 요청).

    예전엔 outputs/[이름]/ 아래 .md 를 전부 평평하게 나열해서, 한 지원 건이 자소서·근거표·
    이력서·포트폴리오까지 네댓 줄로 흩어졌다.
    """

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


class MarkChangesTest(unittest.TestCase):
    """지난 버전에서 다음 버전에 고쳐진 부분 표시 (사용자 요청, 2026-10-12)."""

    def test_deleted_word_is_marked(self):
        marked, n = mark_changes('지난해 중소기업 실무자', '중소기업 실무자')
        self.assertEqual(marked, f'{MARK_OPEN}지난해{MARK_CLOSE} 중소기업 실무자')
        self.assertEqual(n, 1)

    def test_adjacent_changed_words_are_one_span(self):
        marked, n = mark_changes('가 나 다 라', '가 X Y 라')
        self.assertEqual(marked, f'가 {MARK_OPEN}나 다{MARK_CLOSE} 라')
        self.assertEqual(n, 1)

    def test_identical_text_has_no_marks(self):
        self.assertEqual(mark_changes('같은 글\n둘째 줄', '같은 글\n둘째 줄'), ('같은 글\n둘째 줄', 0))

    def test_only_body_text_is_compared(self):
        """머리말(인용), 제목, 글자수, 참조한 경험, 표는 바뀌어도 표시하지 않는다 (2026-10-12 사용자 요청)."""
        old = ('# 회사 업무직원\n> 공고: 화물 관리\n## 지원동기\n글자수: 984자\n\n'
               '본문 첫 문장입니다.\n\n---\n참조한 경험: 해커톤\n| 이름 | 홍길동 |\n**강점선택**: 책임감')
        new = ('# 회사 (최종본)\n> 제출 양식: 이력서\n## 지원동기\n글자수: 1000자\n\n'
               '본문 첫 문장입니다.\n\n---\n참조한 경험: 인턴\n| 이름 | 김철수 |\n**강점선택**: 성실함')
        self.assertEqual(mark_changes(old, new), (old, 0))

    def test_body_change_is_marked_even_when_moved_between_lines(self):
        marked, n = mark_changes('## 문항\n글자수: 1자\n지난해 서비스를\n만들었습니다.',
                                 '## 문항 (수정)\n서비스를 만들었습니다.')
        self.assertEqual(marked, f'## 문항\n글자수: 1자\n{MARK_OPEN}지난해{MARK_CLOSE} 서비스를\n만들었습니다.')
        self.assertEqual(n, 1)

    def test_marks_never_cross_a_line(self):
        marked, _ = mark_changes('가 나\n다 라', '마 바')
        for line in marked.split('\n'):
            self.assertEqual(line.count(MARK_OPEN), line.count(MARK_CLOSE))


class CaseHistoryTest(CaseDirTestBase):
    """건 페이지의 지난 버전: 초안부터 과거 → 최신, 같은 내용은 한 번만."""

    def case(self, folder):
        with self.app.app_context():
            return build_case('홍길동', folder)

    def test_versions_run_oldest_first_and_final_is_not_repeated(self):
        d = self._case('가기업_물류', {'자소서.md': '셋째 초안', '최종본.md': '최종 원고'})
        (d / '자소서').mkdir()
        for i, text in enumerate(['첫째 초안', '둘째 초안', '셋째 초안'], 1):
            (d / '자소서' / f'v{i}.md').write_text(text, encoding='utf-8')
        (d / '최종본').mkdir()
        (d / '최종본' / 'v1.md').write_text('셋째 초안', encoding='utf-8')   # 초안 복사본
        (d / '최종본' / 'v2.md').write_text('최종 원고', encoding='utf-8')

        history = self.case('가기업_물류')['history']
        # 자소서.md 와 최종본/v1 은 바로 앞과 같은 글이라 빠지고, 최종본 자신은 맨 위 블록이다
        self.assertEqual([h['label'] for h in history], ['초안 v1', '초안 v2', '최종본 v1'])
        self.assertEqual(history[0]['content'], f'{MARK_OPEN}첫째{MARK_CLOSE} 초안')
        self.assertEqual(history[-1]['content'], f'{MARK_OPEN}셋째 초안{MARK_CLOSE}')

    def test_without_final_latest_draft_is_last_and_unmarked(self):
        self._case('나기업_기획', {'자소서.md': '초안 본문'})
        case = self.case('나기업_기획')
        self.assertEqual([h['label'] for h in case['history']], ['초안'])
        self.assertEqual(case['history'][0]['changes'], 0)
