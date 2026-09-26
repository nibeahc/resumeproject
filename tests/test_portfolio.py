"""S7-2 포트폴리오 초안 생성기 테스트. LLM 은 스텁, 근거 검증/파일쓰기는 실제로 검증한다."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import db, portfolio  # noqa: E402


def llm_result(text):
    return {'text': text, 'stop_reason': 'end_turn',
            'usage': {'input_tokens': 1, 'output_tokens': 1, 'thinking_tokens': 0}}


def make_output(chapters):
    """chapters: [(num, title, body_without_ref, evidence_file_or_None)]"""
    toc_lines = '\n'.join(f'{n}. {t}' for n, t, _, _ in chapters)
    blocks = [f'===TOC===\n{toc_lines}\n===END===']
    for n, _, body, ef in chapters:
        text = body + (f'\n\n참조한 경험: {ef}' if ef else '')
        blocks.append(f'===CHAPTER: {n}===\n{text}\n===END===')
    return '\n\n'.join(blocks)


EXP_A = """# 팔레트 수요예측 AI

## 메타
- 시기: 2026.01 ~ 2026.03
- 카테고리: 전문성·직무역량

## 상세 내용 (STAR)
### Result (결과)
Validation WMAPE 14.59%를 달성했다. 2,569만 매를 예측했다.
"""

EXP_B = """# HAERO 해커톤

## 메타
- 시기: 2026.06
- 카테고리: 리더십

## 상세 내용 (STAR)
### Result (결과)
최우수상(1등)을 수상했다.
"""


class PortfolioTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / 'app.db'
        self.exp_dir = self.tmp / 'experiences'
        self.output_dir = self.tmp / 'outputs'
        self.exp_dir.mkdir(parents=True)
        db.init_db(self.db_path)
        (self.exp_dir / '2026-팔레트수요예측.md').write_text(EXP_A, encoding='utf-8')
        (self.exp_dir / '2026-HAERO해커톤.md').write_text(EXP_B, encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def gen(self, chapters, **kw):
        with patch.object(portfolio, 'stream_complete', return_value=llm_result(make_output(chapters))):
            return portfolio.generate_portfolio('채빈', self.exp_dir, db_path=self.db_path,
                                                 output_dir=self.output_dir, **kw)

    # ── 후보 선정 ────────────────────────────────────────────
    def test_candidates_fall_back_to_all_experiences_without_job(self):
        r = self.gen([(1, '도입', '요약', None), (2, '팔레트 프로젝트', '내용', '2026-팔레트수요예측.md')])
        self.assertTrue(r.ok)
        self.assertEqual(set(r.candidates), {'2026-팔레트수요예측.md', '2026-HAERO해커톤.md'})

    def test_candidates_from_match_evidence_when_job_given(self):
        conn = db.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json) "
            "VALUES ('manual','0001','테스트기업','데이터분석가','[\"데이터 분석 경험\"]','[]')"
        )
        job_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        conn.execute(
            'INSERT INTO matches (user, job_id, score, required_hit, preferred_hit, evidence_json, missing_json) '
            "VALUES ('채빈', ?, 100, 1.0, 0, ?, '[]')",
            (job_id, json.dumps([{'condition': '데이터 분석 경험', 'kind': '필수', 'verdict': '충족',
                                   'evidence_file': '2026-팔레트수요예측.md', 'note': ''}], ensure_ascii=False)),
        )
        conn.commit()
        conn.close()

        r = self.gen([(1, '도입', '요약', None), (2, '팔레트 프로젝트', '내용', '2026-팔레트수요예측.md')], job_id=job_id)
        self.assertTrue(r.ok)
        self.assertEqual(r.candidates, ['2026-팔레트수요예측.md'])  # HAERO 는 이 공고에서 근거로 안 쓰였으니 후보 아님

    def test_missing_job_returns_error(self):
        r = self.gen([(1, '도입', 'x', None)], job_id=9999)
        self.assertFalse(r.ok)

    def test_no_experiences_returns_error(self):
        empty = self.tmp / 'empty'
        with patch.object(portfolio, 'stream_complete', return_value=llm_result(make_output([]))):
            r = portfolio.generate_portfolio('채빈', empty, db_path=self.db_path, output_dir=self.output_dir)
        self.assertFalse(r.ok)

    # ── 파싱/출력 ────────────────────────────────────────────
    def test_toc_and_chapters_parsed_and_saved(self):
        r = self.gen([
            (1, '도입', '역량 요약입니다.', None),
            (2, '팔레트 프로젝트', 'WMAPE 14.59% 달성.', '2026-팔레트수요예측.md'),
            (3, 'HAERO 해커톤', '최우수상 수상.', '2026-HAERO해커톤.md'),
        ])
        self.assertTrue(r.ok)
        self.assertEqual(len(r.toc), 3)
        self.assertEqual(len(r.chapters), 3)
        self.assertEqual(r.chapters[1].evidence_file, '2026-팔레트수요예측.md')
        self.assertTrue((self.output_dir / '포트폴리오' / 'portfolio-outline.md').exists())
        self.assertIn('WMAPE 14.59%', r.markdown)

    def test_missing_toc_block_fails_cleanly(self):
        with patch.object(portfolio, 'stream_complete', return_value=llm_result('그냥 평문 응답')):
            r = portfolio.generate_portfolio('채빈', self.exp_dir, db_path=self.db_path, output_dir=self.output_dir)
        self.assertFalse(r.ok)
        self.assertIn('TOC', r.error)

    # ── 근거 검증 (핵심: 지어낸 파일명/수치 방지) ─────────────────
    def test_fabricated_evidence_filename_invalidated(self):
        r = self.gen([
            (1, '도입', '요약', None),
            (2, '가짜 프로젝트', '내용', '존재하지않는파일.md'),
        ])
        self.assertTrue(r.ok)
        self.assertIsNone(r.chapters[1].evidence_file)  # 후보에 없는 파일 인용은 무효화됨

    def test_number_not_in_source_flagged_as_ungrounded(self):
        r = self.gen([
            (1, '도입', '요약', None),
            (2, '팔레트 프로젝트', 'WMAPE 99.99%를 달성해 전국 1위를 기록했다.', '2026-팔레트수요예측.md'),
        ])
        self.assertIn('99.99%', r.chapters[1].ungrounded_numbers)
        self.assertIn('확인 필요', r.markdown)

    def test_number_in_source_not_flagged(self):
        r = self.gen([
            (1, '도입', '요약', None),
            (2, '팔레트 프로젝트', 'WMAPE 14.59%를 달성했다.', '2026-팔레트수요예측.md'),
        ])
        self.assertEqual(r.chapters[1].ungrounded_numbers, [])

    def test_intro_chapter_without_reference_not_penalized(self):
        r = self.gen([(1, '도입', '역량 요약, 특별한 수치 없음.', None)])
        self.assertTrue(r.ok)
        self.assertIsNone(r.chapters[0].evidence_file)
        self.assertEqual(r.chapters[0].ungrounded_numbers, [])

    # ── 경로 안전 ────────────────────────────────────────────
    def test_safe_folder_name_reused_from_resume_module(self):
        conn = db.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json) "
            "VALUES ('manual','0001','../../evil','백엔드','[]','[]')"
        )
        job_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        conn.execute(
            "INSERT INTO matches (user, job_id, score, required_hit, preferred_hit, evidence_json, missing_json) "
            "VALUES ('채빈', ?, 0, 0, 0, '[]', '[]')", (job_id,),
        )
        conn.commit()
        conn.close()

        r = self.gen([(1, '도입', 'x', None)], job_id=job_id)
        self.assertTrue(r.ok)
        self.assertFalse((self.tmp / 'evil_백엔드').exists())


if __name__ == '__main__':
    unittest.main()
