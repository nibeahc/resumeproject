"""S5-1 매처 테스트. LLM 은 스텁, 근거 파일 실재 검증/점수 계산/DB 저장은 실제로 검증한다."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import db, matcher  # noqa: E402


def llm_result(text):
    return {'text': text, 'stop_reason': 'end_turn',
            'usage': {'input_tokens': 1, 'output_tokens': 1, 'thinking_tokens': 0}}


def evidence_output(items):
    return f'===EVIDENCE_JSON===\n{json.dumps(items, ensure_ascii=False)}\n===END===\n'


class MatcherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / 'app.db'
        self.exp_dir = self.tmp / 'experiences'
        self.exp_dir.mkdir(parents=True)
        db.init_db(self.db_path)

        (self.exp_dir / '2022-백엔드프로젝트.md').write_text('# 백엔드 프로젝트\n\nPython Django...', encoding='utf-8')
        (self.exp_dir / '2023-데이터분석.md').write_text('# 데이터 분석\n\nSQL 분석...', encoding='utf-8')

        conn = db.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json) "
            "VALUES ('manual','0001','테스트기업','백엔드', '[\"Python 3년 이상\", \"SQL 활용 가능\"]', '[\"AWS 경험\"]')"
        )
        self.job_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_match(self, items):
        with patch.object(matcher, 'stream_complete', return_value=llm_result(evidence_output(items))):
            return matcher.match_job('채빈', self.job_id, self.exp_dir, db_path=self.db_path)

    # ── 정상 경로 ──────────────────────────────────────────────
    def test_full_match_computes_score_and_saves_row(self):
        r = self.run_match([
            {'condition': 'Python 3년 이상', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2022-백엔드프로젝트.md', 'note': ''},
            {'condition': 'SQL 활용 가능', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2023-데이터분석.md', 'note': ''},
            {'condition': 'AWS 경험', 'kind': '우대', 'verdict': '미충족', 'evidence_file': None, 'note': '관련 경험 없음'},
        ])
        self.assertTrue(r.ok)
        self.assertAlmostEqual(r.required_hit, 1.0)
        self.assertAlmostEqual(r.preferred_hit, 0.0)
        self.assertAlmostEqual(r.score, 70.0)  # 1.0*0.7 + 0.0*0.3
        self.assertEqual(r.category, '적합')  # required_hit >= 0.8

        conn = db.get_conn(self.db_path)
        row = conn.execute('SELECT * FROM matches WHERE id = ?', (r.match_id,)).fetchone()
        conn.close()
        self.assertEqual(row['user'], '채빈')
        self.assertEqual(row['job_id'], self.job_id)
        self.assertAlmostEqual(row['score'], 70.0)

    def test_category_thresholds(self):
        def verdicts(req_verdict_a, req_verdict_b):
            return [
                {'condition': 'Python 3년 이상', 'kind': '필수', 'verdict': req_verdict_a,
                 'evidence_file': '2022-백엔드프로젝트.md' if req_verdict_a != '미충족' else None, 'note': ''},
                {'condition': 'SQL 활용 가능', 'kind': '필수', 'verdict': req_verdict_b,
                 'evidence_file': '2023-데이터분석.md' if req_verdict_b != '미충족' else None, 'note': ''},
                {'condition': 'AWS 경험', 'kind': '우대', 'verdict': '미충족', 'evidence_file': None, 'note': ''},
            ]
        r_fit = self.run_match(verdicts('충족', '충족'))
        self.assertEqual(r_fit.category, '적합')

        r_challenge = self.run_match(verdicts('충족', '부분충족'))  # required_hit = 0.75
        self.assertEqual(r_challenge.category, '도전')

        r_unfit = self.run_match(verdicts('미충족', '미충족'))
        self.assertEqual(r_unfit.category, '부적합')

    # ── 1-C: 근거 없는 충족 판정 방지 (핵심) ──────────────────────
    def test_fabricated_evidence_file_demoted_to_unmet(self):
        r = self.run_match([
            {'condition': 'Python 3년 이상', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '없는파일.md', 'note': ''},
            {'condition': 'SQL 활용 가능', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2023-데이터분석.md', 'note': ''},
            {'condition': 'AWS 경험', 'kind': '우대', 'verdict': '미충족', 'evidence_file': None, 'note': ''},
        ])
        first = next(v for v in r.evidence if v.condition == 'Python 3년 이상')
        self.assertEqual(first.verdict, '미충족')
        self.assertTrue(first.demoted)
        self.assertIn('실재하지 않음', first.note)
        self.assertAlmostEqual(r.required_hit, 0.5)  # 하나는 충족, 하나는 강등되어 미충족

    def test_satisfied_without_any_evidence_file_demoted(self):
        r = self.run_match([
            {'condition': 'Python 3년 이상', 'kind': '필수', 'verdict': '충족', 'evidence_file': None, 'note': ''},
            {'condition': 'SQL 활용 가능', 'kind': '필수', 'verdict': '미충족', 'evidence_file': None, 'note': ''},
            {'condition': 'AWS 경험', 'kind': '우대', 'verdict': '미충족', 'evidence_file': None, 'note': ''},
        ])
        first = next(v for v in r.evidence if v.condition == 'Python 3년 이상')
        self.assertEqual(first.verdict, '미충족')
        self.assertTrue(first.demoted)

    def test_partial_credit_with_valid_evidence_not_demoted(self):
        r = self.run_match([
            {'condition': 'Python 3년 이상', 'kind': '필수', 'verdict': '부분충족',
             'evidence_file': '2022-백엔드프로젝트.md', 'note': '기간이 조건보다 짧음'},
            {'condition': 'SQL 활용 가능', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2023-데이터분석.md', 'note': ''},
            {'condition': 'AWS 경험', 'kind': '우대', 'verdict': '미충족', 'evidence_file': None, 'note': ''},
        ])
        first = next(v for v in r.evidence if v.condition == 'Python 3년 이상')
        self.assertEqual(first.verdict, '부분충족')
        self.assertFalse(first.demoted)
        self.assertAlmostEqual(r.required_hit, 0.75)  # (1.0 + 0.5) / 2

    # ── 입력/출력 검증 ────────────────────────────────────────
    def test_missing_job_returns_error(self):
        r = matcher.match_job('채빈', 9999, self.exp_dir, db_path=self.db_path)
        self.assertFalse(r.ok)
        self.assertIn('찾을 수 없습니다', r.error)

    def test_wrong_condition_count_fails_cleanly(self):
        with patch.object(matcher, 'stream_complete', return_value=llm_result(evidence_output([
            {'condition': 'Python 3년 이상', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2022-백엔드프로젝트.md', 'note': ''},
        ]))):
            r = matcher.match_job('채빈', self.job_id, self.exp_dir, db_path=self.db_path)
        self.assertFalse(r.ok)
        self.assertIn('개수', r.error)
        conn = db.get_conn(self.db_path)
        count = conn.execute('SELECT COUNT(*) AS n FROM matches').fetchone()['n']
        conn.close()
        self.assertEqual(count, 0)

    def test_unknown_verdict_value_fails_cleanly(self):
        with patch.object(matcher, 'stream_complete', return_value=llm_result(evidence_output([
            {'condition': 'Python 3년 이상', 'kind': '필수', 'verdict': '거의충족',
             'evidence_file': '2022-백엔드프로젝트.md', 'note': ''},
            {'condition': 'SQL 활용 가능', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2023-데이터분석.md', 'note': ''},
            {'condition': 'AWS 경험', 'kind': '우대', 'verdict': '미충족', 'evidence_file': None, 'note': ''},
        ]))):
            r = matcher.match_job('채빈', self.job_id, self.exp_dir, db_path=self.db_path)
        self.assertFalse(r.ok)

    def test_no_experiences_all_unmet(self):
        empty_dir = self.tmp / 'no_experiences'
        r = self.run_match_with_dir(empty_dir, [
            {'condition': 'Python 3년 이상', 'kind': '필수', 'verdict': '미충족', 'evidence_file': None, 'note': ''},
            {'condition': 'SQL 활용 가능', 'kind': '필수', 'verdict': '미충족', 'evidence_file': None, 'note': ''},
            {'condition': 'AWS 경험', 'kind': '우대', 'verdict': '미충족', 'evidence_file': None, 'note': ''},
        ])
        self.assertTrue(r.ok)
        self.assertAlmostEqual(r.required_hit, 0.0)
        self.assertEqual(r.category, '부적합')

    def run_match_with_dir(self, exp_dir, items):
        with patch.object(matcher, 'stream_complete', return_value=llm_result(evidence_output(items))):
            return matcher.match_job('채빈', self.job_id, exp_dir, db_path=self.db_path)

    def test_rematching_same_job_appends_history_row(self):
        items = [
            {'condition': 'Python 3년 이상', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2022-백엔드프로젝트.md', 'note': ''},
            {'condition': 'SQL 활용 가능', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2023-데이터분석.md', 'note': ''},
            {'condition': 'AWS 경험', 'kind': '우대', 'verdict': '미충족', 'evidence_file': None, 'note': ''},
        ]
        r1 = self.run_match(items)
        r2 = self.run_match(items)
        self.assertNotEqual(r1.match_id, r2.match_id)
        conn = db.get_conn(self.db_path)
        count = conn.execute('SELECT COUNT(*) AS n FROM matches WHERE job_id = ?', (self.job_id,)).fetchone()['n']
        conn.close()
        self.assertEqual(count, 2)


if __name__ == '__main__':
    unittest.main()
