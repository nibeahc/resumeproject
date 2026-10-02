"""lib/coverletter.py 단위 테스트 (S11-7).

이 파일의 존재 자체가 추출의 목적이다 — Flask 앱도, HTTP 도, 임시 서버도 없이
생성 엔진을 직접 돌린다. tests/test_resume.py 와 같은 모양이다.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import coverletter as C  # noqa: E402


def llm_result(text):
    return {'text': text, 'stop_reason': 'end_turn',
            'usage': {'input_tokens': 1, 'output_tokens': 1, 'thinking_tokens': 0}}


def fake_complete(answer='가' * 97, meta='참조한 경험: 테스트'):   # 100자 목표의 5% 이내
    def _complete(system=None, messages=None, max_tokens=None, on_delta=None):
        return llm_result(f'===ANSWER===\n{answer}\n===END===\n===META===\n{meta}\n===END===\n')
    return _complete


def bounded(n, count_mode='포함'):
    return C.LengthSpec(mode='bounded', hard_max_chars=n, count_mode=count_mode)


UNLIMITED = C.LengthSpec(mode='unlimited', hard_max_chars=None, count_mode='포함')


class LengthSpecTest(unittest.TestCase):
    def test_bounded_and_count_desc(self):
        self.assertTrue(bounded(100).bounded)
        self.assertFalse(UNLIMITED.bounded)
        self.assertEqual(bounded(100, '제외').count_desc, '공백 제외')


class StripPlaceholderRulesTest(unittest.TestCase):
    def test_keeps_real_rules_drops_examples(self):
        out = C.strip_placeholder_rules('## 표현 규칙\n\n- 진짜 규칙\n- (예: 안내문)\n')
        self.assertIn('진짜 규칙', out)
        self.assertNotIn('안내문', out)

    def test_empty_when_only_examples(self):
        self.assertEqual(C.strip_placeholder_rules('## 구조\n\n- (예: 안내)\n'), '')


class JobToTextTest(unittest.TestCase):
    """S11-8: jobs 테이블 전체를 넣으면 id, content_hash 같은 내부 값까지 프롬프트에 섞인다."""

    def test_extracts_only_useful_fields(self):
        text = C._job_to_text({
            'id': 7, 'content_hash': 'abc123', 'company': '테스트기업', 'position': '백엔드',
            'required_json': '["Python 3년"]', 'preferred_json': '["AWS"]',
        })
        self.assertIn('테스트기업', text)
        self.assertIn('Python 3년', text)
        self.assertIn('AWS', text)
        self.assertNotIn('abc123', text)
        self.assertNotIn('content_hash', text)

    def test_malformed_json_does_not_raise(self):
        text = C._job_to_text({'company': 'x', 'required_json': '깨진 JSON'})
        self.assertIn('x', text)

    def test_empty_job_is_empty_string(self):
        self.assertEqual(C._job_to_text({}), '')


class GenerateWithoutFlaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.user_root = self.tmp / 'users' / '홍길동'
        self.exp_dir = self.user_root / 'experiences'
        self.exp_dir.mkdir(parents=True)
        (self.exp_dir / '2024-경험.md').write_text(
            '# 테스트 경험\n\n## 메타\n\n- 카테고리: 리더십\n- 키워드: 리더십\n\n내용', encoding='utf-8')
        self.companies = self.tmp / 'companies'
        self.companies.mkdir()
        (self.companies / '기업.md').write_text('# 기업\n\n핵심가치: 정확성', encoding='utf-8')
        self.references = self.tmp / 'references' / 'coverletter'
        self.references.mkdir(parents=True)
        self.out = self.tmp / 'outputs' / '홍길동'
        self.out.mkdir(parents=True)
        # S11-9: 초안도 documents 테이블에 기록된다. db_path 를 안 주면 lib.db 가
        # 실제 프로젝트 DB(data/app.db)를 쓴다 — 실제로 이 파일을 처음 돌렸을 때 그랬다.
        self.db_path = self.tmp / 'app.db'
        from lib import db
        db.init_db(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ctx(self, **kw):
        base = dict(companies_dir=self.companies, references_dir=self.references,
                    company='기업', position='직무')
        base.update(kw)
        return C.gather_context(self.user_root, **base)

    def run_events(self, **kw):
        params = dict(company='기업', position='직무', length=bounded(100),
                      context=self.ctx(), output_dir=self.out, db_path=self.db_path,
                      complete=fake_complete())
        params.update(kw)
        return list(C.generate_coverletter_events('홍길동', '지원 동기', self.exp_dir, **params))

    def test_does_not_touch_real_project_db(self):
        """S11-9 회귀 방지: db_path 를 안 넘기면 lib.db 가 실제 data/app.db 를 쓴다.
        이 파일을 처음 돌렸을 때 실제로 홍길동 행 3개가 프로젝트 DB 에 들어갔다."""
        from lib import db as real_db
        self.run_events()
        conn = real_db.get_conn()          # db_path 생략 → 실제 프로젝트 DB
        try:
            n = conn.execute("SELECT COUNT(*) AS c FROM documents WHERE user = '홍길동'").fetchone()['c']
        finally:
            conn.close()
        self.assertEqual(n, 0)

    def test_saves_version_and_documents_row(self):
        """S11-9: 예전엔 사람이 편집기에서 저장하기 전까지 버전 이력이 없었다."""
        from lib import db as real_db, versions
        events = self.run_events()
        data = events[-1][1]
        self.assertEqual(data['version'], 1)
        self.assertTrue((self.out / '기업_직무' / '자소서' / 'v1.md').exists())
        conn = real_db.get_conn(self.db_path)
        try:
            rows = conn.execute("SELECT kind, version FROM documents WHERE user = '홍길동'").fetchall()
        finally:
            conn.close()
        self.assertEqual([dict(r) for r in rows], [{'kind': 'coverletter', 'version': 1}])
        self.assertEqual(len(versions.list_versions(self.out, '기업_직무/자소서.md', '홍길동',
                                                     db_path=self.db_path)), 1)

    def test_second_question_adds_version_two(self):
        self.run_events()
        list(C.generate_coverletter_events(
            '홍길동', '두 번째 문항', self.exp_dir, company='기업', position='직무',
            length=bounded(100), context=self.ctx(), output_dir=self.out,
            db_path=self.db_path, complete=fake_complete()))
        self.assertTrue((self.out / '기업_직무' / '자소서' / 'v2.md').exists())
        text = (self.out / '기업_직무' / '자소서.md').read_text(encoding='utf-8')
        self.assertIn('지원 동기', text)       # 앞 문항이 보존되어야 한다
        self.assertIn('두 번째 문항', text)

    def test_cited_unknown_experience_flagged(self):
        """S11-9: 모델이 주지 않은 파일을 인용했다고 보고하면 사람이 봐야 한다."""
        events = self.run_events(
            complete=fake_complete(meta='참조한 경험: 없는 경험 (2099-가짜.md)'))
        data = events[-1][1]
        self.assertEqual(data['status'], C.STATUS_REVIEW_REQUIRED)
        self.assertIn(C.ISSUE_CITED_UNKNOWN, data['issues'])
        self.assertEqual(data['cited_experiences'], ['2099-가짜.md'])
        self.assertEqual(data['used_experiences'], ['2024-경험.md'])

    def test_correct_citation_is_ready(self):
        events = self.run_events(complete=fake_complete(meta='참조한 경험: 테스트 경험 (2024-경험.md)'))
        data = events[-1][1]
        self.assertEqual(data['status'], C.STATUS_READY)
        self.assertEqual(data['cited_experiences'], ['2024-경험.md'])

    def test_citation_without_filename_is_not_penalised(self):
        """파일명을 안 적는 형식도 있다. 대조할 대상이 없을 뿐 감점 사유는 아니다."""
        events = self.run_events(complete=fake_complete(meta='참조한 경험: 테스트 경험'))
        data = events[-1][1]
        self.assertEqual(data['status'], C.STATUS_READY)
        self.assertEqual(data['cited_experiences'], [])

    def test_generates_and_saves_without_app_context(self):
        events = self.run_events()
        kind, data = events[-1]
        self.assertEqual(kind, 'done')
        self.assertEqual(data['status'], C.STATUS_READY)
        self.assertEqual(data['rel_path'], '기업_직무/자소서.md')
        self.assertTrue((self.out / '기업_직무' / '자소서.md').exists())

    def test_output_dir_none_skips_writing(self):
        """lib/resume.generate_resume 와 같은 규약 — 저장 없이 결과만 받는다."""
        events = self.run_events(output_dir=None)
        kind, data = events[-1]
        self.assertEqual(kind, 'done')
        self.assertIsNone(data['rel_path'])
        self.assertIsNone(data['saved_to'])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_missing_company_blocks(self):
        (self.companies / '기업.md').unlink()
        events = self.run_events(context=self.ctx())
        self.assertEqual(events[-1][0], 'error')
        self.assertEqual(events[-1][1]['needs_input'], 'company_research')

    def test_unlimited_skips_retry(self):
        events = self.run_events(length=UNLIMITED, complete=fake_complete('가' * 900))
        kind, data = events[-1]
        self.assertEqual(kind, 'done')
        self.assertFalse(data['exceeded'])
        self.assertNotIn('retry', [d.get('stage') for _, d in events if isinstance(d, dict)])

    def test_job_text_flows_into_prompt(self):
        seen = {}

        def capture(system=None, messages=None, max_tokens=None, on_delta=None):
            seen['prompt'] = messages[0]['content']
            return llm_result('===ANSWER===\n답변\n===END===\n===META===\nx\n===END===\n')

        self.run_events(context=self.ctx(job={'company': '기업', 'required_json': '["조건A"]'}),
                        complete=capture)
        self.assertIn('조건A', seen['prompt'])

    def test_gather_context_reads_user_files(self):
        (self.user_root / 'profile.md').write_text('내 프로필', encoding='utf-8')
        (self.user_root / 'writing-rules.md').write_text('- 진짜 규칙\n', encoding='utf-8')
        ctx = self.ctx()
        self.assertEqual(ctx.profile_text, '내 프로필')
        self.assertIn('진짜 규칙', ctx.rules_text)
        self.assertIn('정확성', ctx.company_text)

    def test_folder_escape_is_refused(self):
        events = self.run_events(folder_name='../탈출')
        self.assertEqual(events[-1][0], 'error')
        self.assertFalse((self.tmp / 'outputs' / '탈출').exists())


if __name__ == '__main__':
    unittest.main()
