"""S5-2 매칭 결과 UI 테스트. lib.matcher.match_job 은 스텁, DB_PATH 는 임시 파일로 격리한다."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import db  # noqa: E402
from lib.matcher import MatchResult  # noqa: E402
from web import create_app  # noqa: E402
from web.routes import matches as matches_route  # noqa: E402


def insert_job(conn, company, position, closed=0):
    conn.execute(
        "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json, closed) "
        "VALUES ('manual', ?, ?, ?, '[]', '[]', ?)",
        (company, company, position, closed),
    )
    return conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']


def insert_match(conn, user, job_id, required_hit, preferred_hit, score, evidence=None, missing=None):
    conn.execute(
        'INSERT INTO matches (user, job_id, score, required_hit, preferred_hit, evidence_json, missing_json) '
        'VALUES (?,?,?,?,?,?,?)',
        (user, job_id, score, required_hit, preferred_hit,
         json.dumps(evidence or [], ensure_ascii=False), json.dumps(missing or [], ensure_ascii=False)),
    )


class MatchesRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        self.db_path = self.tmp / 'app.db'
        (self.root / 'users' / '홍길동' / 'experiences').mkdir(parents=True)
        (self.root / 'users' / '홍길동' / 'experiences' / 'a.md').write_text('# 경험A', encoding='utf-8')
        db.init_db(self.db_path)
        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.db_path, 'TESTING': True})
        self.c = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_page_renders_with_no_matches(self):
        r = self.c.get('/matches/홍길동')
        self.assertEqual(r.status_code, 200)
        self.assertIn('아직 매칭 결과가 없습니다'.encode('utf-8'), r.data)

    def test_only_latest_row_per_job_shown(self):
        conn = db.get_conn(self.db_path)
        job_id = insert_job(conn, '테스트기업', '백엔드')
        insert_match(conn, '홍길동', job_id, 0.3, 0.0, 21.0)   # 오래된(낮은) 매칭
        insert_match(conn, '홍길동', job_id, 0.9, 1.0, 93.0)   # 재매칭(최신, 더 나은 점수)
        conn.commit()
        conn.close()

        r = self.c.get('/matches/홍길동')
        body = r.get_data(as_text=True)
        self.assertIn('93.0', body)
        self.assertNotIn('21.0', body)

    def test_sorted_fit_before_stretch_before_unfit(self):
        conn = db.get_conn(self.db_path)
        j1 = insert_job(conn, '부적합기업', '직무1')
        j2 = insert_job(conn, '적합기업', '직무2')
        j3 = insert_job(conn, '도전기업', '직무3')
        insert_match(conn, '홍길동', j1, 0.2, 0.0, 14.0)   # 부적합
        insert_match(conn, '홍길동', j2, 0.9, 0.5, 78.0)   # 적합
        insert_match(conn, '홍길동', j3, 0.6, 0.0, 42.0)   # 도전
        conn.commit()
        conn.close()

        r = self.c.get('/matches/홍길동')
        body = r.get_data(as_text=True)
        pos_fit = body.index('적합기업')
        pos_stretch = body.index('도전기업')
        pos_unfit = body.index('부적합기업')
        self.assertLess(pos_fit, pos_stretch)
        self.assertLess(pos_stretch, pos_unfit)

    def test_reason_and_linked_experiences_derived_from_evidence(self):
        conn = db.get_conn(self.db_path)
        job_id = insert_job(conn, '테스트기업', '백엔드')
        insert_match(conn, '홍길동', job_id, 1.0, 0.0, 70.0, evidence=[
            {'condition': '조건1', 'kind': '필수', 'verdict': '충족', 'evidence_file': 'a.md', 'note': ''},
            {'condition': '조건2', 'kind': '우대', 'verdict': '미충족', 'evidence_file': None, 'note': '없음'},
        ], missing=[{'condition': '조건2', 'kind': '우대', 'note': '없음'}])
        conn.commit()
        conn.close()

        r = self.c.get('/matches/홍길동')
        body = r.get_data(as_text=True)
        self.assertIn('필수 1/1', body)
        self.assertIn('우대 0/1', body)
        self.assertIn('a.md', body)
        self.assertIn('조건2', body)

    # ── /run (SSE) ────────────────────────────────────────────
    def test_run_streams_progress_and_writes_rows_via_matcher(self):
        conn = db.get_conn(self.db_path)
        job_id = insert_job(conn, '테스트기업', '백엔드')
        conn.commit()
        conn.close()

        # 스트리밍 응답은 지연 평가된다 — patch 블록 밖에서 get_data() 를 부르면 이미 patch 가
        # 풀린 뒤라 실제 stream_complete() 가 불려 API 비용이 들 수 있다(과거 실측으로 확인된
        # 함정). 반드시 patch 블록 안에서 읽는다.
        with patch.object(matches_route.matcher, 'match_job', return_value=MatchResult(
                ok=True, job_id=job_id, user='홍길동', company='테스트기업', position='백엔드',
                score=80.0, required_hit=1.0, preferred_hit=0.5, category='적합')):
            r = self.c.post('/matches/홍길동/run')
            body = r.get_data(as_text=True)
        self.assertIn('event: progress', body)
        self.assertIn('event: done', body)
        self.assertIn('테스트기업', body)

    def test_run_with_no_open_jobs_errors_via_sse(self):
        r = self.c.post('/matches/홍길동/run')
        body = r.get_data(as_text=True)
        self.assertIn('event: error', body)
        self.assertIn('매칭할 공고가 없습니다', body)

    def test_run_excludes_closed_jobs(self):
        conn = db.get_conn(self.db_path)
        insert_job(conn, '마감된기업', '직무', closed=1)
        conn.commit()
        conn.close()
        r = self.c.post('/matches/홍길동/run')
        self.assertIn('매칭할 공고가 없습니다', r.get_data(as_text=True))

    def test_run_without_experiences_errors_via_sse(self):
        conn = db.get_conn(self.db_path)
        insert_job(conn, '테스트기업', '백엔드')
        conn.commit()
        conn.close()
        (self.root / 'users' / '홍길동' / 'experiences' / 'a.md').unlink()

        r = self.c.post('/matches/홍길동/run')
        self.assertIn('경험 파일이 없습니다', r.get_data(as_text=True))

    def test_run_with_specific_job_id_matches_only_that_job(self):
        conn = db.get_conn(self.db_path)
        j1 = insert_job(conn, '기업1', '직무1')
        insert_job(conn, '기업2', '직무2')
        conn.commit()
        conn.close()

        with patch.object(matches_route.matcher, 'match_job', return_value=MatchResult(
                ok=True, job_id=j1, user='홍길동', company='기업1', position='직무1',
                score=50.0, required_hit=0.5, preferred_hit=0.0, category='도전')) as m:
            r = self.c.post(f'/matches/홍길동/run?job_id={j1}')
            body = r.get_data(as_text=True)
        m.assert_called_once()
        self.assertEqual(m.call_args.args[1], j1)
        self.assertIn('기업1', body)
        self.assertNotIn('기업2', body)

    def test_run_rejects_non_numeric_job_id_before_streaming(self):
        r = self.c.post('/matches/홍길동/run?job_id=../../etc')
        self.assertEqual(r.status_code, 400)

    # ── 고도화(2026-09-26): 매칭 결과 화면에서 바로 지원 상태 등록/변경 ────────────────
    def test_unregistered_job_shows_no_status_badge_but_offers_registration(self):
        conn = db.get_conn(self.db_path)
        job_id = insert_job(conn, '테스트기업', '백엔드')
        insert_match(conn, '홍길동', job_id, 1.0, 0.0, 70.0)
        conn.commit()
        conn.close()

        r = self.c.get('/matches/홍길동')
        body = r.get_data(as_text=True)
        self.assertIn('미등록', body)
        self.assertIn('관심 등록...', body)

    def test_registered_job_shows_current_status_badge(self):
        from lib import applications
        conn = db.get_conn(self.db_path)
        job_id = insert_job(conn, '테스트기업', '백엔드')
        insert_match(conn, '홍길동', job_id, 1.0, 0.0, 70.0)
        conn.commit()
        conn.close()
        applications.set_status('홍길동', job_id, '준비중', db_path=self.db_path)

        r = self.c.get('/matches/홍길동')
        body = r.get_data(as_text=True)
        self.assertIn('status-준비중', body)
        self.assertIn('상태 변경...', body)

    def test_status_change_from_matches_page_updates_applications(self):
        from lib import applications
        conn = db.get_conn(self.db_path)
        job_id = insert_job(conn, '테스트기업', '백엔드')
        insert_match(conn, '홍길동', job_id, 1.0, 0.0, 70.0)
        conn.commit()
        conn.close()

        r = self.c.post(f'/applications/홍길동/{job_id}/status', json={'status': '관심'})
        self.assertTrue(r.get_json()['success'])

        history = applications.get_history('홍길동', job_id, db_path=self.db_path)
        self.assertEqual([h.to_status for h in history], ['관심'])


if __name__ == '__main__':
    unittest.main()
