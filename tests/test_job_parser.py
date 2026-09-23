"""S4-2 공고 파서 테스트. LLM 은 스텁, DB 저장/중복판정/파일쓰기는 실제로 검증한다."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import db, job_parser  # noqa: E402


def llm_result(text):
    return {'text': text, 'stop_reason': 'end_turn',
            'usage': {'input_tokens': 1, 'output_tokens': 1, 'thinking_tokens': 0}}


def make_output(markdown='# 테스트기업 백엔드 개발자\n\n(본문)', required=None, preferred=None, meta=None):
    required = required if required is not None else ['컴퓨터공학 전공 또는 동등 경력', 'Python 3년 이상']
    preferred = preferred if preferred is not None else ['AWS 경험']
    meta = meta if meta is not None else {'company': '테스트기업', 'position': '백엔드 개발자',
                                           'deadline': '2026-12-31', 'employment_type': '정규직',
                                           'career': '경력 3년', 'location': '서울'}
    return (
        f'===JOB_MARKDOWN===\n{markdown}\n===END===\n\n'
        f'===REQUIRED_JSON===\n{json.dumps(required, ensure_ascii=False)}\n===END===\n\n'
        f'===PREFERRED_JSON===\n{json.dumps(preferred, ensure_ascii=False)}\n===END===\n\n'
        f'===META_JSON===\n{json.dumps(meta, ensure_ascii=False)}\n===END===\n'
    )


class JobParserTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / 'app.db'
        self.jobs_dir = self.tmp / 'jobs'
        db.init_db(self.db_path)
        self.raw = ('백엔드 개발자 채용\n\n[자격요건]\n- 컴퓨터공학 전공 또는 동등 경력\n'
                    '- Python 3년 이상\n\n[우대사항]\n- AWS 경험\n\n마감일: 2026-12-31')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def parse(self, raw=None, **kw):
        with patch.object(job_parser, 'stream_complete', return_value=llm_result(make_output(**kw))):
            return job_parser.parse_and_save(raw or self.raw, jobs_dir=self.jobs_dir, db_path=self.db_path)

    # ── 정상 경로 ──────────────────────────────────────────────
    def test_saves_markdown_file_and_db_row(self):
        r = self.parse()
        self.assertTrue(r.ok)
        self.assertFalse(r.duplicate)
        self.assertEqual(r.source_job_id, '0001')
        self.assertEqual(r.path, 'jobs/manual-0001.md')
        self.assertTrue((self.jobs_dir / 'manual-0001.md').exists())

        conn = db.get_conn(self.db_path)
        row = conn.execute('SELECT * FROM jobs WHERE id = ?', (r.job_id,)).fetchone()
        conn.close()
        self.assertEqual(row['company'], '테스트기업')
        self.assertEqual(row['position'], '백엔드 개발자')
        self.assertEqual(json.loads(row['required_json']), ['컴퓨터공학 전공 또는 동등 경력', 'Python 3년 이상'])
        self.assertEqual(json.loads(row['preferred_json']), ['AWS 경험'])
        self.assertEqual(row['deadline'], '2026-12-31')

    def test_required_and_preferred_are_separate_arrays(self):
        """S4-2 완료 조건의 핵심: 필수/우대가 하나로 섞이지 않고 배열로 분리된다."""
        r = self.parse(required=['조건A', '조건B', '조건C'], preferred=['우대A'])
        self.assertEqual(len(r.required), 3)
        self.assertEqual(len(r.preferred), 1)
        self.assertNotIn('우대A', r.required)

    def test_sequential_manual_ids(self):
        r1 = self.parse()
        r2 = self.parse(raw=self.raw + '\n\n(완전히 다른 두 번째 공고 본문, 프론트엔드 채용)',
                         markdown='# 두번째 공고', meta={'company': '다른회사', 'position': '프론트엔드'})
        self.assertEqual(r1.source_job_id, '0001')
        self.assertFalse(r2.duplicate)
        self.assertEqual(r2.source_job_id, '0002')

    # ── 중복 판정 ──────────────────────────────────────────────
    def test_duplicate_paste_skips_llm_call(self):
        self.parse()
        with patch.object(job_parser, 'stream_complete') as m:
            r2 = job_parser.parse_and_save(self.raw, jobs_dir=self.jobs_dir, db_path=self.db_path)
        m.assert_not_called()
        self.assertTrue(r2.duplicate)
        self.assertEqual(r2.company, '테스트기업')

    def test_whitespace_only_difference_still_counts_as_duplicate(self):
        self.parse()
        reformatted = '  \n\n'.join(self.raw.split('\n'))
        with patch.object(job_parser, 'stream_complete') as m:
            r2 = job_parser.parse_and_save(reformatted, jobs_dir=self.jobs_dir, db_path=self.db_path)
        m.assert_not_called()
        self.assertTrue(r2.duplicate)

    def test_different_content_not_treated_as_duplicate(self):
        self.parse()
        r2 = self.parse(raw=self.raw + ' 완전히 다른 추가 내용입니다 상세페이지 참고',
                         meta={'company': '다른회사', 'position': '다른직무'})
        self.assertFalse(r2.duplicate)

    # ── 입력 검증 / 실패 경로 ────────────────────────────────────
    def test_too_short_raw_text_rejected(self):
        r = job_parser.parse_and_save('짧은 글', jobs_dir=self.jobs_dir, db_path=self.db_path)
        self.assertFalse(r.ok)
        self.assertIn('짧습니다', r.error)

    def test_missing_output_blocks_fails_cleanly(self):
        with patch.object(job_parser, 'stream_complete', return_value=llm_result('그냥 평문 대답입니다.')):
            r = job_parser.parse_and_save(self.raw, jobs_dir=self.jobs_dir, db_path=self.db_path)
        self.assertFalse(r.ok)
        self.assertIn('블록', r.error)
        conn = db.get_conn(self.db_path)
        count = conn.execute('SELECT COUNT(*) AS n FROM jobs').fetchone()['n']
        conn.close()
        self.assertEqual(count, 0)

    def test_malformed_json_block_fails_cleanly_without_writing(self):
        bad = make_output().replace('["AWS 경험"]', '[AWS 경험 (따옴표 없음)]')
        with patch.object(job_parser, 'stream_complete', return_value=llm_result(bad)):
            r = job_parser.parse_and_save(self.raw, jobs_dir=self.jobs_dir, db_path=self.db_path)
        self.assertFalse(r.ok)
        self.assertFalse(any(self.jobs_dir.glob('*.md')) if self.jobs_dir.exists() else False)

    def test_missing_company_and_position_without_hints_fails(self):
        r = self.parse(meta={'company': '', 'position': '', 'deadline': None})
        self.assertFalse(r.ok)
        self.assertIn('회사명', r.error)

    def test_hints_used_when_llm_cannot_find_company_position(self):
        r = self.parse(meta={'company': '', 'position': '', 'deadline': None})
        self.assertFalse(r.ok)  # 힌트 없이는 실패

        with patch.object(job_parser, 'stream_complete',
                           return_value=llm_result(make_output(meta={'company': '', 'position': '', 'deadline': None}))):
            r2 = job_parser.parse_and_save(self.raw, company_hint='힌트기업', position_hint='힌트직무',
                                            jobs_dir=self.jobs_dir, db_path=self.db_path)
        self.assertTrue(r2.ok)
        self.assertEqual(r2.company, '힌트기업')
        self.assertEqual(r2.position, '힌트직무')

    def test_invalid_deadline_format_stored_as_null_not_fabricated(self):
        r = self.parse(meta={'company': '테스트기업', 'position': '백엔드', 'deadline': '아마 내년쯤'})
        self.assertTrue(r.ok)
        conn = db.get_conn(self.db_path)
        row = conn.execute('SELECT deadline FROM jobs WHERE id = ?', (r.job_id,)).fetchone()
        conn.close()
        self.assertIsNone(row['deadline'])

    def test_url_never_fetched(self):
        """url 인자를 줘도 이 모듈이 네트워크로 그 URL 을 요청하지 않는다 — 프롬프트에만 참고용으로 들어간다."""
        with patch('urllib.request.urlopen') as fake_fetch, \
             patch.object(job_parser, 'stream_complete', return_value=llm_result(make_output())):
            r = job_parser.parse_and_save(self.raw, url='https://example.com/job/123',
                                           jobs_dir=self.jobs_dir, db_path=self.db_path)
        self.assertTrue(r.ok)
        fake_fetch.assert_not_called()


class NormalizeMarkdownMetaTest(unittest.TestCase):
    """S4-2 실측에서 발견: LLM 이 공고 ID(템플릿 예시값을 베낌)와 수집일(오늘 날짜를 알 방법이
    없어 매번 다르게 지어냄)을 실제 값과 다르게 채웠다. 코드가 강제로 덮어써야 한다."""

    def test_overwrites_wrong_job_id_and_date(self):
        md = ('# 회사 직무\n\n## 메타\n\n- 출처: manual\n- 공고 ID: manual-0001\n'
              '- 원문 URL:\n- 수집일: 2099.01.01\n- 마감일: 상시\n')
        out = job_parser._normalize_markdown_meta(md, 'manual', '0007')
        self.assertIn('- 공고 ID: manual-0007', out)
        self.assertNotIn('manual-0001', out)
        self.assertNotIn('2099.01.01', out)
        from datetime import date
        self.assertIn(f"- 수집일: {date.today().strftime('%Y.%m.%d')}", out)


class ContentHashTest(unittest.TestCase):
    def test_same_text_same_hash(self):
        self.assertEqual(job_parser.content_hash('abc def'), job_parser.content_hash('abc def'))

    def test_whitespace_normalized(self):
        self.assertEqual(job_parser.content_hash('abc\n\ndef  '), job_parser.content_hash('abc def'))

    def test_different_text_different_hash(self):
        self.assertNotEqual(job_parser.content_hash('abc'), job_parser.content_hash('abd'))


if __name__ == '__main__':
    unittest.main()
