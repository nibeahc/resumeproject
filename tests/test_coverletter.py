"""lib/coverletter.py 단위 테스트 (S11-7).

이 파일의 존재 자체가 추출의 목적이다 — Flask 앱도, HTTP 도, 임시 서버도 없이
생성 엔진을 직접 돌린다. tests/test_resume.py 와 같은 모양이다.
"""
import shutil
import sys
import tempfile
import unittest
from datetime import date
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


class EngineFixture:
    """임시 사용자/기업/출력 디렉터리와 호출 헬퍼. 이 클래스 자체는 TestCase 가 아니다 —
    TestCase 를 상속하면 아래 테스트 클래스들이 기반 테스트까지 매번 다시 돌린다."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.user_root = self.tmp / 'users' / '홍길동'
        self.exp_dir = self.user_root / 'experiences'
        self.exp_dir.mkdir(parents=True)
        (self.exp_dir / '2024-경험.md').write_text(
            '# 테스트 경험\n\n## 메타\n\n- 카테고리: 리더십\n- 키워드: 리더십\n\n내용', encoding='utf-8')
        self.companies = self.tmp / 'companies'
        self.companies.mkdir()
        # S11-11: 기업명·확인일·출처가 있어야 조사 완료로 본다.
        (self.companies / '기업.md').write_text(
            '# 기업\n\n'
            f'> 확인일: {date.today().isoformat()}\n'
            '> 출처: https://example.com/about\n\n'
            '핵심가치: 정확성\n', encoding='utf-8')
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
        # S11-10: 검토 호출은 전용 테스트(QualityCheckTest)에서만 켠다.
        params = dict(company='기업', position='직무', length=bounded(100),
                      context=self.ctx(), output_dir=self.out, db_path=self.db_path,
                      quality_check=False, complete=fake_complete())
        params.update(kw)
        return list(C.generate_coverletter_events('홍길동', '지원 동기', self.exp_dir, **params))


class GenerateWithoutFlaskTest(EngineFixture, unittest.TestCase):
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


PLAN_OK = ('===PLAN===\n'
           'p1 | 정확성이 신뢰로 이어진다 | 지원 이유 | 2024-경험.md | 오류를 줄인 경험이 그 판단의 근거다 | - | -\n'
           '===END===')


def draft(answer='가' * 97, plan=PLAN_OK, meta='참조한 경험: 테스트 경험 (2024-경험.md)'):
    body = f'{plan}\n' if plan else ''
    return llm_result(f'{body}===ANSWER===\n{answer}\n===END===\n===META===\n{meta}\n===END===\n')


def review(issues='[]', sentences=None, claim='정확성이 신뢰로 이어진다', paragraphs=None):
    sentences = sentences or '[{"id": "s1", "role": "claim", "supports": "p1", "fact_ids": []}]'
    paragraphs = paragraphs or '[{"id": "p1", "inferred_claim": "정확성이 신뢰로 이어진다"}]'
    return llm_result(
        '===REVIEW===\n'
        '{"inferred_claim": "%s", "paragraphs": %s, "sentences": %s, "issues": %s}\n'
        '===END===\n' % (claim, paragraphs, sentences, issues))


class ScriptedLLM:
    """호출 순서대로 미리 정한 응답을 돌려준다. system 프롬프트도 기록해
    검토가 정말 별도 스킬로 불렸는지 확인한다."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.systems = []
        self.messages = []

    def __call__(self, system=None, messages=None, max_tokens=None, on_delta=None):
        self.systems.append(system or '')
        self.messages.append(messages)
        return self.responses.pop(0) if self.responses else draft()

    @property
    def calls(self):
        return len(self.systems)


class QualityCheckTest(EngineFixture, unittest.TestCase):
    """S11-10 / 설계검토 B. 호출 수가 설계 제약이다 — 최악 4회, 끄면 2회."""

    def run_quality(self, llm, **kw):
        params = dict(quality_check=True, complete=llm)
        params.update(kw)
        return self.run_events(**params)

    def test_review_runs_in_separate_call_with_its_own_skill(self):
        llm = ScriptedLLM(draft(), review())
        events = self.run_quality(llm)
        self.assertEqual(llm.calls, 2)                       # 초안 1 + 검토 1
        self.assertIn('자소서 초안 검토', llm.systems[1])      # review-coverletter.md
        self.assertNotEqual(llm.systems[0], llm.systems[1])
        # 검토 입력에 계획을 넣지 않는다 — 초안에서 논지를 역으로 읽게 한다 (B-2)
        self.assertNotIn('===PLAN===', llm.messages[1][0]['content'])
        self.assertEqual(events[-1][1]['status'], C.STATUS_READY)

    def test_plan_is_requested_and_parsed(self):
        llm = ScriptedLLM(draft(), review())
        data = self.run_quality(llm)[-1][1]
        self.assertIn('===PLAN===', llm.messages[0][0]['content'])
        self.assertEqual(len(data['plan']), 1)
        self.assertEqual(data['plan'][0]['experiences'], ['2024-경험.md'])

    def test_missing_plan_is_flagged(self):
        data = self.run_quality(ScriptedLLM(draft(plan=None), review()))[-1][1]
        self.assertIn(C.ISSUE_PLAN_MISSING, data['issues'])
        self.assertEqual(data['status'], C.STATUS_REVIEW_REQUIRED)

    def test_plan_citing_unknown_experience_is_flagged(self):
        bad = '===PLAN===\np1 | 주장 | 요구 | 2099-없는경험.md | 이유 | - | -\n===END==='
        data = self.run_quality(ScriptedLLM(draft(plan=bad), review()))[-1][1]
        self.assertIn(C.ISSUE_PLAN_UNKNOWN_EXPERIENCE, data['issues'])

    def test_blocking_issue_triggers_one_repair(self):
        bad = ('[{"sentence_ids": ["s1"], "kind": "unsupported_claim", '
               '"reason": "행동 근거가 없다"}]')
        llm = ScriptedLLM(draft(), review(issues=bad), draft(answer='나' * 97))
        data = self.run_quality(llm)[-1][1]
        self.assertEqual(llm.calls, 3)            # 초안 + 검토 + 수정
        self.assertEqual(data['repairs'], 1)
        self.assertIn('나', data['answer'])
        # 수정 프롬프트는 "더 잘 써라" 가 아니라 문제 문장과 유형을 준다 (B-3)
        repair_msg = llm.messages[2][-1]['content']
        self.assertIn('unsupported_claim', repair_msg)
        self.assertIn('s1', repair_msg)
        self.assertIn('2024-경험.md', repair_msg)

    def test_repair_is_rejected_if_it_breaks_length(self):
        """평가 지적만 고치고 글자수가 틀어진 수정본으로 바꾸지 않는다."""
        bad = ('[{"sentence_ids": ["s1"], "kind": "unsupported_claim", "reason": "근거 없음"}]')
        llm = ScriptedLLM(draft(), review(issues=bad), draft(answer='다' * 400))
        events = self.run_quality(llm)
        data = events[-1][1]
        self.assertIn('repair_rejected', [d.get('stage') for _, d in events if isinstance(d, dict)])
        self.assertNotIn('다', data['answer'])     # 원래 초안을 유지
        self.assertFalse(data['exceeded'])

    def test_review_with_wrong_sentence_ids_is_treated_as_failed(self):
        """엉뚱한 문장을 가리키는 지적은 고칠 수가 없다 (B-2)."""
        llm = ScriptedLLM(draft(), review(sentences='[{"id": "s99", "role": "claim"}]'))
        data = self.run_quality(llm)[-1][1]
        self.assertIn(C.ISSUE_REVIEW_FAILED, data['issues'])
        self.assertIsNotNone(data['review_error'])
        self.assertEqual(data['status'], C.STATUS_REVIEW_REQUIRED)

    def test_unparseable_review_does_not_lose_the_draft(self):
        llm = ScriptedLLM(draft(), llm_result('검토 못 하겠습니다'))
        data = self.run_quality(llm)[-1][1]
        self.assertIn(C.ISSUE_REVIEW_FAILED, data['issues'])
        self.assertTrue(data['answer'])
        self.assertTrue((self.out / '기업_직무' / '자소서.md').exists())

    def test_toggle_off_keeps_two_calls_and_no_plan(self):
        llm = ScriptedLLM(draft(plan=None))
        data = self.run_events(quality_check=False, complete=llm)[-1][1]
        self.assertEqual(llm.calls, 1)
        self.assertNotIn('===PLAN===', llm.messages[0][0]['content'])
        self.assertIsNone(data['review'])
        self.assertEqual(data['status'], C.STATUS_READY)

    def test_worst_case_is_four_calls(self):
        """초안 1 + 글자수 재시도 1 + 검토 1 + 수정 1. 크레딧 소진 사고 때문에 상한을 둔다."""
        bad = '[{"sentence_ids": ["s1"], "kind": "unsupported_claim", "reason": "x"}]'
        llm = ScriptedLLM(draft(answer='가' * 300),     # 초과 → 재시도
                          draft(answer='가' * 97),
                          review(issues=bad),
                          draft(answer='나' * 97))
        self.run_quality(llm)
        self.assertEqual(llm.calls, 4)


class SecondExperienceTest(EngineFixture, unittest.TestCase):
    """설계검토 B-1: "둘 다 분석 경험" 은 두 번째 경험을 쓸 이유가 아니다."""

    def setUp(self):
        super().setUp()
        (self.exp_dir / '2023-둘째.md').write_text(
            '# 둘째 경험\n\n## 메타\n\n- 카테고리: 리더십\n- 키워드: 리더십\n\n내용', encoding='utf-8')

    def test_two_experiences_without_a_stated_role_is_flagged(self):
        plan = ('===PLAN===\np1 | 주장 | 요구 | 2024-경험.md, 2023-둘째.md | 둘 다 같은 판단의 근거다 | - | -\n===END===')
        llm = ScriptedLLM(draft(plan=plan), review())
        data = self.run_events(quality_check=True, complete=llm,
                               override_filenames=['2024-경험.md', '2023-둘째.md'])[-1][1]
        self.assertIn(C.ISSUE_SECOND_EXPERIENCE_UNJUSTIFIED, data['issues'])

    def test_two_experiences_with_a_role_passes(self):
        plan = ('===PLAN===\np1 | 주장 | 요구 | 2024-경험.md, 2023-둘째.md | 둘 다 같은 판단의 근거다 | - | 대조 — 혼자 할 때와 팀일 때의 차이\n===END===')
        llm = ScriptedLLM(draft(plan=plan), review())
        data = self.run_events(quality_check=True, complete=llm,
                               override_filenames=['2024-경험.md', '2023-둘째.md'])[-1][1]
        self.assertNotIn(C.ISSUE_SECOND_EXPERIENCE_UNJUSTIFIED, data['issues'])


class CompanyEvidenceTest(unittest.TestCase):
    """S11-11 / 설계검토 A-4: "파일이 있기만 하면 통과" 를 막는다."""

    def doc(self, body, when=None):
        when = when or date.today()
        return f'# 테스트기업\n\n> 확인일: {when.isoformat()}\n> 출처: https://ex.com/a\n\n{body}'

    def test_well_formed_passes(self):
        self.assertEqual(C.check_company_evidence('테스트기업', self.doc('가치: 정확성')), [])

    def test_empty_file(self):
        self.assertEqual(C.check_company_evidence('테스트기업', '   '), ['empty'])

    def test_other_company_content(self):
        other = self.doc('가치: 정확성').replace('테스트기업', '다른기업')
        self.assertIn('mismatch', C.check_company_evidence('테스트기업', other))

    def test_company_name_variants_are_tolerated(self):
        """㈜, 공백 같은 표기 차이로 오탐하면 안 된다."""
        text = self.doc('가치').replace('테스트기업', '테스트기업㈜')
        self.assertNotIn('mismatch', C.check_company_evidence('테스트 기업', text))

    def test_stale_file(self):
        from datetime import timedelta
        old = date.today() - timedelta(days=C.COMPANY_EVIDENCE_MAX_AGE_DAYS + 1)
        self.assertIn('stale', C.check_company_evidence('테스트기업', self.doc('가치', when=old)))

    def test_undated_file(self):
        self.assertIn('undated', C.check_company_evidence(
            '테스트기업', '# 테스트기업\n\n> 출처: https://ex.com/a\n\n가치'))

    def test_no_source_url(self):
        self.assertIn('no_source', C.check_company_evidence(
            '테스트기업', f'# 테스트기업\n\n> 확인일: {date.today().isoformat()}\n\n가치'))


class UnverifiedFactsTest(unittest.TestCase):
    """S11-11 / 설계검토 A-1: 수치·기관명 불일치 탐지. 경보이지 허위 판정이 아니다."""

    def test_number_absent_from_sources_is_flagged(self):
        nums, _ = C.find_unverified_facts('매출을 37% 늘렸습니다.', ['원문에는 12% 라고만 적혀 있다'])
        self.assertEqual(nums, ['37'])

    def test_number_present_in_sources_is_not_flagged(self):
        nums, _ = C.find_unverified_facts('매출을 37% 늘렸습니다.', ['성과: 매출 37% 증가'])
        self.assertEqual(nums, [])

    def test_thousands_separator_is_normalised(self):
        nums, _ = C.find_unverified_facts('1,200건을 처리했습니다.', ['처리 건수 1200건'])
        self.assertEqual(nums, [])

    def test_single_digits_are_ignored_as_noise(self):
        nums, _ = C.find_unverified_facts('첫 번째로 3가지를 했습니다.', ['내용 없음'])
        self.assertEqual(nums, [])

    def test_unknown_organization_is_flagged(self):
        _, orgs = C.find_unverified_facts('한국산업안전보건공단과 협업했습니다.', ['다른 내용'])
        self.assertEqual(orgs, ['한국산업안전보건공단'])

    def test_known_organization_is_not_flagged(self):
        _, orgs = C.find_unverified_facts('인천대학교에서 수행했습니다.', ['기관명: 인천대학교'])
        self.assertEqual(orgs, [])


class PersonalRuleLintTest(unittest.TestCase):
    """S11-11: writing-rules.md 에 적혀 있어도 코드가 검사하지 않던 규칙."""

    RULES = '- 가운뎃점(·) 사용 금지. 대체 우선순위: ...'

    def test_middot_flagged_when_rule_present(self):
        self.assertEqual(C.check_personal_rules('데이터·분석을 했다', self.RULES), ['가운뎃점(·)'])

    def test_clean_text_passes(self):
        self.assertEqual(C.check_personal_rules('데이터와 분석을 했다', self.RULES), [])

    def test_not_checked_when_user_has_no_such_rule(self):
        """모든 사용자에게 강제할 규칙이 아니다. 규칙이 없으면 검사도 하지 않는다."""
        self.assertEqual(C.check_personal_rules('데이터·분석', '- 다른 규칙만 있다'), [])


class GlossaryTest(unittest.TestCase):
    """S11-12 / 설계검토 C-2, D-3: 기준이 "영어인가" 가 아니라 "독자가 아는가" 여야 한다."""

    DOC = """# 사전

## 풀어 써야 하는 이름

| 원래 이름 | 자소서 표현 | 비고 |
|---|---|---|
| DIVE | 교내 데이터 분석 소모임 | 첫 등장에 한 번만 |
| 다온 | 교내 봉사 동아리 |  |

## 그대로 써도 되는 이름

- 현대글로비스
- 인천대학교

## 분야에서 통용되는 전문용어

- SCM, ERP
"""

    def test_parses_both_sections(self):
        g = C.parse_glossary(self.DOC)
        self.assertEqual(g.explain['DIVE'], '교내 데이터 분석 소모임')
        self.assertIn('현대글로비스', g.allowed)
        self.assertIn('SCM', g.allowed)

    def test_korean_internal_name_is_caught_too(self):
        """예전 규칙은 "영어 워딩" 기준이라 한국어 소모임명을 놓쳤다."""
        g = C.parse_glossary(self.DOC)
        self.assertEqual(C.check_obscure_names('다온에서 활동했습니다', g), ['다온'])

    def test_name_with_explanation_passes(self):
        g = C.parse_glossary(self.DOC)
        self.assertEqual(C.check_obscure_names('교내 데이터 분석 소모임 DIVE 에서', g), [])

    def test_explanation_only_passes(self):
        g = C.parse_glossary(self.DOC)
        self.assertEqual(C.check_obscure_names('교내 데이터 분석 소모임에서 활동', g), [])

    def test_bare_name_is_flagged(self):
        g = C.parse_glossary(self.DOC)
        self.assertEqual(C.check_obscure_names('DIVE 에서 활동했습니다', g), ['DIVE'])

    def test_allowed_names_are_never_flagged(self):
        g = C.parse_glossary(self.DOC)
        self.assertEqual(C.check_obscure_names('현대글로비스에서 인턴을 했습니다', g), [])

    def test_empty_glossary_is_harmless(self):
        self.assertEqual(C.check_obscure_names('무슨 말이든', C.parse_glossary('')), [])
        self.assertFalse(C.parse_glossary(''))


SAMPLE_DOC = """# 기업 직무

## 지원 동기
글자수: 97자 (공백 포함, 목표 100자)

첫 번째 답변입니다.

---
참조한 경험: 테스트 경험 (2024-경험.md)
작성일: 2026.10.01

## 성장 경험
글자수: 50자 (공백 포함, 목표 100자)

두 번째 답변입니다.

---
참조한 경험: 테스트 경험 (2024-경험.md)
작성일: 2026.10.01
"""


class DocumentStructureTest(unittest.TestCase):
    """S11-13: 수정은 한 문항만 바꾸고 전체를 돌려준다."""

    def test_splits_sections(self):
        head, sections = C.split_document(SAMPLE_DOC)
        self.assertEqual(head, '# 기업 직무')
        self.assertEqual([q for q, _ in sections], ['지원 동기', '성장 경험'])

    def test_section_answer_strips_header_and_meta(self):
        section = C.find_section(SAMPLE_DOC, '지원 동기')
        self.assertEqual(C.section_answer(section), '첫 번째 답변입니다.')

    def test_section_cited_files(self):
        section = C.find_section(SAMPLE_DOC, '지원 동기')
        self.assertEqual(C.section_cited_files(section), ['2024-경험.md'])

    def test_replace_section_preserves_the_others(self):
        out = C.replace_section(SAMPLE_DOC, '지원 동기', '지원 동기\n글자수: 10자\n\n고친 답변\n')
        self.assertIn('고친 답변', out)
        self.assertNotIn('첫 번째 답변', out)
        self.assertIn('두 번째 답변입니다.', out)   # 건드리지 않은 문항은 보존
        self.assertTrue(out.startswith('# 기업 직무'))

    def test_replace_unknown_section_returns_none(self):
        self.assertIsNone(C.replace_section(SAMPLE_DOC, '없는 문항', 'x'))


class ReviseTest(EngineFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.doc_dir = self.out / '기업_직무'
        self.doc_dir.mkdir(parents=True)
        (self.doc_dir / '자소서.md').write_text(SAMPLE_DOC, encoding='utf-8')

    def revise(self, llm, **kw):
        question = kw.pop('question', '지원 동기')
        params = dict(rel_path='기업_직무/자소서.md', instruction='더 구체적으로',
                      length=bounded(100), context=self.ctx(), output_dir=self.out,
                      db_path=self.db_path, complete=llm)
        params.update(kw)
        return list(C.revise_coverletter_events('홍길동', question, self.exp_dir, **params))

    def test_returns_full_document_not_just_the_edit(self):
        """설계검토 C-3 의 핵심. 발췌가 아니라 전체를 돌려준다."""
        events = self.revise(ScriptedLLM(draft(answer='고친 답변입니다. ' + '가' * 88, plan=None)))
        kind, data = events[-1]
        self.assertEqual(kind, 'done')
        self.assertIn('고친 답변입니다', data['full_document'])
        self.assertIn('두 번째 답변입니다.', data['full_document'])   # 다른 문항 보존
        self.assertTrue(data['full_document'].startswith('# 기업 직무'))

    def test_saves_a_new_version(self):
        self.revise(ScriptedLLM(draft(answer='고친 답변입니다. ' + '가' * 88, plan=None)))
        self.assertTrue((self.doc_dir / '자소서' / 'v1.md').exists())
        on_disk = (self.doc_dir / '자소서.md').read_text(encoding='utf-8')
        self.assertIn('고친 답변입니다', on_disk)
        self.assertIn('두 번째 답변입니다.', on_disk)

    def test_unknown_question_errors_with_available_list(self):
        events = self.revise(ScriptedLLM(draft()), question='없는 문항')
        kind, data = events[-1]
        self.assertEqual(kind, 'error')
        self.assertIn('성장 경험', data['available_questions'])

    def test_path_outside_output_dir_is_refused(self):
        events = self.revise(ScriptedLLM(draft()), rel_path='../../etc/passwd')
        self.assertEqual(events[-1][0], 'error')

    def test_deterministic_checks_still_apply(self):
        """지시를 따르느라 글자수가 틀어지면 알린다."""
        events = self.revise(ScriptedLLM(draft(answer='가' * 400, plan=None)))
        data = events[-1][1]
        self.assertTrue(data['exceeded'])
        self.assertEqual(data['status'], C.STATUS_REVIEW_REQUIRED)

    def test_allowed_experiences_come_from_the_document(self):
        """수정하면서 새 경험이 끼어들면 안 된다 — 원문에 기록된 것만 허용한다."""
        llm = ScriptedLLM(draft(answer='가' * 97, plan=None))
        self.revise(llm)
        prompt = llm.messages[0][0]['content']
        self.assertIn('2024-경험.md', prompt)
        self.assertIn('새 경험이나 새 사실을 넣지 마세요', prompt)


class PlanSchemaTest(unittest.TestCase):
    """S11-14 / 설계검토 B-1: 근거와 주장을 잇는 설명(warrant)이 없으면 논증이 아니라 나열이다."""

    def row(self, warrant='오류를 줄인 경험이 그 판단의 근거다', jd='-', second='-'):
        return (f'===PLAN===\np1 | 주장 | 요구 | 2024-경험.md | {warrant} | {jd} | {second}\n'
                '===END===')

    def test_parses_warrant_and_job_requirement(self):
        rows = C.parse_plan(self.row(jd='SOP 준수 경험'))
        self.assertEqual(rows[0]['warrant'], '오류를 줄인 경험이 그 판단의 근거다')
        self.assertEqual(rows[0]['job_requirement'], 'SOP 준수 경험')

    def test_dash_means_empty(self):
        self.assertEqual(C.parse_plan(self.row(jd='-'))[0]['job_requirement'], '')

    def test_missing_warrant_is_flagged(self):
        rows = C.parse_plan(self.row(warrant='-'))
        self.assertIn(C.ISSUE_PLAN_MISSING_WARRANT, C.lint_plan(rows, ['2024-경험.md']))

    def test_present_warrant_passes(self):
        rows = C.parse_plan(self.row())
        self.assertNotIn(C.ISSUE_PLAN_MISSING_WARRANT, C.lint_plan(rows, ['2024-경험.md']))


class ReviewSchemaTest(unittest.TestCase):
    """S11-14 / 설계검토 B-3: 문단 구분과 근거 출처까지 검증한다."""

    def parse(self, body, ids=('s1',), facts=('2024-경험.md',)):
        return C.parse_review('===REVIEW===\n' + body + '\n===END===', ids, allowed_fact_ids=facts)

    def test_valid_review(self):
        data, err = self.parse('{"paragraphs": [{"id": "p1"}], '
                                '"sentences": [{"id": "s1", "role": "claim", "supports": "p1", '
                                '"fact_ids": ["2024-경험.md"]}], "issues": []}')
        self.assertIsNone(err)
        self.assertEqual(data['sentences'][0]['supports'], 'p1')

    def test_sentence_pointing_at_unknown_paragraph_fails(self):
        _, err = self.parse('{"paragraphs": [{"id": "p1"}], '
                             '"sentences": [{"id": "s1", "role": "claim", "supports": "p9"}], '
                             '"issues": []}')
        self.assertIn('없는 문단', err)

    def test_fact_id_outside_supplied_experiences_fails(self):
        """모델이 주지 않은 경험을 근거로 댔다면 그 평가 자체를 믿을 수 없다."""
        _, err = self.parse('{"paragraphs": [{"id": "p1"}], '
                             '"sentences": [{"id": "s1", "role": "evidence", "supports": "p1", '
                             '"fact_ids": ["2099-없는경험.md"]}], "issues": []}')
        self.assertIn('제공하지 않은 근거', err)

    def test_null_supports_is_allowed(self):
        """어느 주장에도 기여하지 않는 문장이 있다는 것 자체가 유효한 판정이다."""
        _, err = self.parse('{"paragraphs": [{"id": "p1"}], '
                             '"sentences": [{"id": "s1", "role": "unrelated", "supports": null}], '
                             '"issues": []}')
        self.assertIsNone(err)


class GlossaryFactInteractionTest(EngineFixture, unittest.TestCase):
    """실제 자소서를 쓰다 발견한 오탐. glossary 가 풀어 쓰라고 한 표현은 경험 원문에
    그대로 없으므로, 그대로 두면 번역한 이름이 매번 '미확인 기관' 으로 잡힌다."""

    def setUp(self):
        super().setUp()
        (self.user_root / 'glossary.md').write_text(
            '## 풀어 써야 하는 이름\n\n| 원래 이름 | 자소서 표현 |\n|---|---|\n'
            '| Hanze University of Applied Sciences | 한제대학교 |\n', encoding='utf-8')
        (self.exp_dir / '2024-경험.md').write_text(
            '# 교환학생\n\n## 메타\n\n- 기관명: Hanze University of Applied Sciences\n\n내용',
            encoding='utf-8')

    def test_glossary_gloss_counts_as_verified(self):
        answer = '한제대학교에서 공부했습니다. ' + '가' * 83
        llm = ScriptedLLM(draft(answer=answer, plan=None))
        data = self.run_events(complete=llm, length=bounded(len(answer)))[-1][1]
        self.assertNotIn(C.ISSUE_UNKNOWN_ORG, data['issues'])
        self.assertEqual(data['unverified_organizations'], [])


class DateFalsePositiveTest(unittest.TestCase):
    """실사용 중 발견. 경험 원문의 "2026.01.12" 는 _NUMBER 에 "2026.01" 로 잡혀서,
    초안의 "2026년" 이 미확인 수치로 걸렸다. 날짜는 환산·표기 차이일 뿐 창작이 아니다."""

    def test_year_from_dotted_date_is_verified(self):
        nums, _ = C.find_unverified_facts('2026년 1월부터 근무했습니다.',
                                           ['- 시기: 2026.01.12 ~ 2026.02.27'])
        self.assertEqual(nums, [])

    def test_korean_date_form_also_works(self):
        nums, _ = C.find_unverified_facts('2024년에 시작했습니다.', ['2024년 8월부터'])
        self.assertEqual(nums, [])

    def test_a_year_that_is_nowhere_is_still_flagged(self):
        """오탐만 줄이고 진짜 미확인 수치는 그대로 잡아야 한다."""
        nums, _ = C.find_unverified_facts('2019년에 수상했습니다.', ['- 시기: 2026.01.12'])
        self.assertEqual(nums, ['2019'])
