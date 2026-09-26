"""S7-1 이력서 생성기 테스트. LLM 을 쓰지 않는 순수 코드라 스텁 없이 전부 실제 동작을 검증한다."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import db, resume  # noqa: E402


def insert_job(conn, company, position, required):
    conn.execute(
        "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json) "
        "VALUES ('manual', ?, ?, ?, ?, '[]')",
        (company, company, position, json.dumps(required, ensure_ascii=False)),
    )
    return conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']


def insert_match(conn, user, job_id, evidence):
    conn.execute(
        'INSERT INTO matches (user, job_id, score, required_hit, preferred_hit, evidence_json, missing_json) '
        'VALUES (?,?,?,?,?,?,?)',
        (user, job_id, 0, 0, 0, json.dumps(evidence, ensure_ascii=False), '[]'),
    )


EXP_BACKEND = """# 백엔드 인턴

## 메타

- 시기: 2022.06 ~ 2022.12
- 기관명: 스타트업A
- 역할: 백엔드 개발 인턴

## 상세 내용 (STAR)

### Situation (상황)

결제 API 개발 인력이 부족했다.

### Action (내가 한 것)

Django 로 결제 API 15개를 설계, 구현했다.

### Result (결과)

API 응답 시간을 60% 단축했다.
"""

EXP_DATA = """# 데이터 분석 프로젝트

## 메타

- 시기: 2023.03 ~ 2023.06
- 기관명: 교내 학회
- 역할: 분석 담당

## 상세 내용 (STAR)

### Result (결과)

매출 데이터 20만 건을 분석해 학회 우수상을 받았다.
"""

EXP_LEADER = """# 동아리 회장

## 메타

- 시기: 2021.03 ~ 2022.02
- 기관명: 교내 동아리
- 역할: 회장

## 상세 내용 (STAR)

### Result (결과)

참여율을 40%에서 85%로 끌어올렸다.
"""


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / 'app.db'
        self.exp_dir = self.tmp / 'experiences'
        self.output_dir = self.tmp / 'outputs'
        self.exp_dir.mkdir(parents=True)
        db.init_db(self.db_path)

        (self.exp_dir / '2022-백엔드인턴.md').write_text(EXP_BACKEND, encoding='utf-8')
        (self.exp_dir / '2023-데이터분석.md').write_text(EXP_DATA, encoding='utf-8')
        (self.exp_dir / '2021-동아리회장.md').write_text(EXP_LEADER, encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── 핵심: 같은 경험 DB, 다른 공고 → 다른 순서 ────────────────
    def test_different_jobs_produce_different_order_from_same_experience_db(self):
        conn = db.get_conn(self.db_path)
        job_backend = insert_job(conn, '백엔드기업', '백엔드 개발자',
                                  ['Python 백엔드 경험', '팀 리더십 경험'])
        insert_match(conn, '채빈', job_backend, [
            {'condition': 'Python 백엔드 경험', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2022-백엔드인턴.md', 'note': ''},
            {'condition': '팀 리더십 경험', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2021-동아리회장.md', 'note': ''},
        ])

        job_pm = insert_job(conn, 'PM기업', 'PM', ['팀 리더십 경험', 'Python 백엔드 경험'])
        insert_match(conn, '채빈', job_pm, [
            {'condition': '팀 리더십 경험', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2021-동아리회장.md', 'note': ''},
            {'condition': 'Python 백엔드 경험', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2022-백엔드인턴.md', 'note': ''},
        ])
        conn.commit()
        conn.close()

        r1 = resume.generate_resume('채빈', job_backend, self.exp_dir, db_path=self.db_path)
        r2 = resume.generate_resume('채빈', job_pm, self.exp_dir, db_path=self.db_path)

        self.assertTrue(r1.ok and r2.ok)
        self.assertEqual(r1.order, ['2022-백엔드인턴.md', '2021-동아리회장.md'])
        self.assertEqual(r2.order, ['2021-동아리회장.md', '2022-백엔드인턴.md'])
        self.assertNotEqual(r1.order, r2.order)  # ROADMAP S7-1 완료 조건의 핵심

    def test_markdown_reflects_required_order_and_content_verbatim(self):
        conn = db.get_conn(self.db_path)
        job_id = insert_job(conn, '테스트기업', '백엔드', ['Python 백엔드 경험'])
        insert_match(conn, '채빈', job_id, [
            {'condition': 'Python 백엔드 경험', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2022-백엔드인턴.md', 'note': ''},
        ])
        conn.commit()
        conn.close()

        r = resume.generate_resume('채빈', job_id, self.exp_dir, db_path=self.db_path)
        self.assertTrue(r.ok)
        self.assertIn('백엔드 인턴', r.markdown)
        self.assertIn('API 응답 시간을 60% 단축했다.', r.markdown)  # Result 원문 그대로, 새로 안 씀
        self.assertIn('대응 조건: 1', r.markdown)

    def test_unmatched_required_condition_listed_not_fabricated(self):
        conn = db.get_conn(self.db_path)
        job_id = insert_job(conn, '테스트기업', '백엔드', ['AWS 자격증 소지'])
        insert_match(conn, '채빈', job_id, [
            {'condition': 'AWS 자격증 소지', 'kind': '필수', 'verdict': '미충족',
             'evidence_file': None, 'note': '관련 경험 없음'},
        ])
        conn.commit()
        conn.close()

        r = resume.generate_resume('채빈', job_id, self.exp_dir, db_path=self.db_path)
        self.assertTrue(r.ok)
        self.assertEqual(r.order, [])
        self.assertEqual(r.unmatched_conditions, ['AWS 자격증 소지'])

    def test_reused_evidence_across_multiple_conditions_listed_once(self):
        conn = db.get_conn(self.db_path)
        job_id = insert_job(conn, '테스트기업', '백엔드', ['조건A', '조건B'])
        insert_match(conn, '채빈', job_id, [
            {'condition': '조건A', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2022-백엔드인턴.md', 'note': ''},
            {'condition': '조건B', 'kind': '필수', 'verdict': '부분충족',
             'evidence_file': '2022-백엔드인턴.md', 'note': ''},
        ])
        conn.commit()
        conn.close()

        r = resume.generate_resume('채빈', job_id, self.exp_dir, db_path=self.db_path)
        self.assertEqual(r.order, ['2022-백엔드인턴.md'])  # 중복 없이 한 번만
        self.assertIn('대응 조건: 1, 2', r.markdown)

    def test_experiences_not_used_as_evidence_listed_as_other(self):
        conn = db.get_conn(self.db_path)
        job_id = insert_job(conn, '테스트기업', '백엔드', ['Python 백엔드 경험'])
        insert_match(conn, '채빈', job_id, [
            {'condition': 'Python 백엔드 경험', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2022-백엔드인턴.md', 'note': ''},
        ])
        conn.commit()
        conn.close()

        r = resume.generate_resume('채빈', job_id, self.exp_dir, db_path=self.db_path)
        self.assertIn('2023-데이터분석.md', r.markdown)
        self.assertIn('2021-동아리회장.md', r.markdown)

    # ── 저장 경로 ──────────────────────────────────────────────
    def test_writes_to_output_dir_with_company_position_folder(self):
        conn = db.get_conn(self.db_path)
        job_id = insert_job(conn, '테스트기업', '백엔드', ['Python 백엔드 경험'])
        insert_match(conn, '채빈', job_id, [
            {'condition': 'Python 백엔드 경험', 'kind': '필수', 'verdict': '충족',
             'evidence_file': '2022-백엔드인턴.md', 'note': ''},
        ])
        conn.commit()
        conn.close()

        r = resume.generate_resume('채빈', job_id, self.exp_dir, db_path=self.db_path, output_dir=self.output_dir)
        self.assertTrue(r.ok)
        self.assertEqual(r.path, '테스트기업_백엔드/이력서.md')
        self.assertTrue((self.output_dir / '테스트기업_백엔드' / '이력서.md').exists())

    def test_unsafe_company_name_does_not_escape_output_dir(self):
        conn = db.get_conn(self.db_path)
        job_id = insert_job(conn, '../../evil', '백엔드', [])
        insert_match(conn, '채빈', job_id, [])
        conn.commit()
        conn.close()

        r = resume.generate_resume('채빈', job_id, self.exp_dir, db_path=self.db_path, output_dir=self.output_dir)
        self.assertTrue(r.ok)
        self.assertFalse((self.tmp / 'evil_백엔드').exists())
        self.assertFalse((self.tmp.parent / 'evil_백엔드').exists())
        # 위험 문자가 제거된 폴더명으로 output_dir 안에만 생겼어야 한다
        created = list(self.output_dir.iterdir())
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].resolve().is_relative_to(self.output_dir.resolve()))

    # ── 입력 검증 ────────────────────────────────────────────
    def test_missing_job_returns_error(self):
        r = resume.generate_resume('채빈', 9999, self.exp_dir, db_path=self.db_path)
        self.assertFalse(r.ok)

    def test_no_match_yet_returns_error_not_fabricated_resume(self):
        conn = db.get_conn(self.db_path)
        job_id = insert_job(conn, '테스트기업', '백엔드', ['조건1'])
        conn.commit()
        conn.close()

        r = resume.generate_resume('채빈', job_id, self.exp_dir, db_path=self.db_path)
        self.assertFalse(r.ok)
        self.assertIn('매칭 결과가 없습니다', r.error)


if __name__ == '__main__':
    unittest.main()
