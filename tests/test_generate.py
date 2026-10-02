"""S1-3 생성 라우트 테스트. 핵심은 글자수를 코드로 검증하는 부분이다 (LLM 을 믿지 않는다)."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import portfolio as portfolio_module  # noqa: E402
from web import create_app  # noqa: E402
from web.routes import generate as generate_route  # noqa: E402


def llm_result(text):
    return {'text': text, 'stop_reason': 'end_turn',
            'usage': {'input_tokens': 1, 'output_tokens': 1, 'thinking_tokens': 0}}


class CountCharsTest(unittest.TestCase):
    def test_포함_counts_everything(self):
        self.assertEqual(generate_route.count_chars('가 나 다', '포함'), 5)

    def test_제외_strips_all_whitespace(self):
        self.assertEqual(generate_route.count_chars('가 나\n다  라', '제외'), 4)

    def test_ignores_llm_self_reported_count(self):
        # S1-1 실측: 모델이 683자라 보고했지만 실제론 715자였다. count_chars 는 본문만 본다.
        answer, meta = generate_route.parse_answer(
            '===ANSWER===\n' + ('가' * 100) + '\n===END===\n===META===\n참조한 경험: x\n===END===\n'
        )
        self.assertEqual(generate_route.count_chars(answer, '포함'), 100)


class ParseAnswerTest(unittest.TestCase):
    def test_extracts_answer_and_meta_separately(self):
        text = '===ANSWER===\n본문입니다\n===END===\n\n===META===\n참조한 경험: A (a.md)\n===END===\n'
        answer, meta = generate_route.parse_answer(text)
        self.assertEqual(answer, '본문입니다')
        self.assertEqual(meta, '참조한 경험: A (a.md)')

    def test_missing_blocks_return_empty(self):
        answer, meta = generate_route.parse_answer('형식을 안 지킨 텍스트')
        self.assertEqual((answer, meta), ('', ''))


class StripPlaceholderRulesTest(unittest.TestCase):
    """S11-5: writing-rules.md 의 "(예: ...)" 안내 줄이 실제 규칙으로 프롬프트에 실리던 문제.

    예전 코드는 파일 *전체* 가 비었을 때만 치환해서, 템플릿 예시가 한 줄이라도 남아 있으면
    그대로 모델에 갔다. 실측: "- (예: 문항당 최대 3문장, 핵심만)" 이 스킬의
    "글자수에 최대한 가깝게 채우되" 와 충돌하고 있었다.
    """

    def test_removes_placeholder_bullets(self):
        out = generate_route.strip_placeholder_rules(
            '## 표현 규칙\n\n- 진짜 규칙이다\n- (예: "~했어요" 체 사용 금지)\n')
        self.assertIn('진짜 규칙이다', out)
        self.assertNotIn('~했어요', out)

    def test_keeps_real_rule_that_contains_an_inline_example(self):
        """괄호 예시가 줄 중간에 있는 진짜 규칙은 남아야 한다 (채빈 파일의 영어 워딩 규칙)."""
        rule = '- 영어 워딩을 한글로 풀어 쓸 것 (예: "Korean Workshop" → "워크숍")'
        out = generate_route.strip_placeholder_rules('## 표현 규칙\n\n' + rule + '\n')
        self.assertIn('Korean Workshop', out)

    def test_drops_sections_left_empty(self):
        out = generate_route.strip_placeholder_rules(
            '## 표현 규칙\n\n- 진짜 규칙\n\n## 선호하는 문단 길이\n\n- (예: 문항당 최대 3문장)\n')
        self.assertIn('표현 규칙', out)
        self.assertNotIn('선호하는 문단 길이', out)
        self.assertNotIn('3문장', out)

    def test_all_placeholders_yields_empty(self):
        out = generate_route.strip_placeholder_rules(
            '# 규칙\n\n## 구조\n\n- (예: 어쩌고)\n\n## 금지\n\n-\n')
        self.assertEqual(out, '')

    def test_prompt_does_not_carry_placeholders(self):
        prompt = generate_route.build_prompt(
            '홍길동', '기업', '직무', '문항', 100, '포함',
            '프로필', '문체', '## 선호하는 문단 길이\n\n- (예: 문항당 최대 3문장, 핵심만)\n',
            '관심직무', '', [], [('a.md', '경험')])
        self.assertNotIn('3문장', prompt)
        self.assertIn('적용할 규칙 없음', prompt)


class GeneratePageTest(unittest.TestCase):
    """S1-4: GET /generate/<name> 페이지 렌더링."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        (self.root / 'users' / '홍길동').mkdir(parents=True)
        # S7-3: GET /generate/<name> 이 공고 드롭다운을 채우려고 jobs 테이블을 본다 — DB_PATH 를
        # 임시 경로로 주지 않으면 실제 프로젝트 data/app.db 를 건드리게 된다.
        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.tmp / 'app.db', 'TESTING': True})
        self.c = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_renders_with_no_experiences(self):
        r = self.c.get('/generate/홍길동')
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn('아직 경험 DB가 없습니다', body)
        self.assertIn('id="genBtn"', body)
        self.assertIn('onclick="generate()" disabled', body)  # 버튼 태그 자체가 비활성화되어야 함

    def test_renders_with_experiences_enabled(self):
        exp_dir = self.root / 'users' / '홍길동' / 'experiences'
        exp_dir.mkdir()
        (exp_dir / 'a.md').write_text('x', encoding='utf-8')
        r = self.c.get('/generate/홍길동')
        body = r.get_data(as_text=True)
        self.assertIn('경험 1개를 바탕으로', body)
        self.assertNotIn('onclick="generate()" disabled', body)  # 버튼이 활성화되어 있어야 함

    def test_unknown_user_redirects_home(self):
        r = self.c.get('/generate/없는사람')
        self.assertEqual(r.status_code, 302)

    def test_renders_candidate_selection_ui(self):
        """S3-2: 후보 경험 체크박스 영역과 재생성 버튼이 페이지에 있어야 한다."""
        exp_dir = self.root / 'users' / '홍길동' / 'experiences'
        exp_dir.mkdir()
        (exp_dir / 'a.md').write_text('x', encoding='utf-8')
        r = self.c.get('/generate/홍길동')
        body = r.get_data(as_text=True)
        self.assertIn('id="candidatesBox"', body)
        self.assertIn('id="candList"', body)
        self.assertIn('id="regenBtn"', body)
        self.assertIn('onclick="regenerate()"', body)

    def test_job_dropdown_empty_without_jobs(self):
        r = self.c.get('/generate/홍길동')
        body = r.get_data(as_text=True)
        self.assertIn('id="jobSelect"', body)
        self.assertIn('직접 입력', body)

    def test_job_dropdown_lists_open_jobs_only(self):
        """S7-3: 공고 드롭다운은 마감 안 된 공고만 보여준다."""
        from lib import db
        db.init_db(self.tmp / 'app.db')
        conn = db.get_conn(self.tmp / 'app.db')
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json, closed) "
            "VALUES ('manual','0001','열린회사','백엔드','[]','[]', 0)")
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json, closed) "
            "VALUES ('manual','0002','마감회사','프론트엔드','[]','[]', 1)")
        conn.commit()
        conn.close()

        r = self.c.get('/generate/홍길동')
        body = r.get_data(as_text=True)
        self.assertIn('열린회사', body)
        self.assertNotIn('마감회사', body)
        self.assertIn('id="bundleBtn"', body)


class GenerateRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        u = self.root / 'users' / '홍길동'
        (u / 'experiences').mkdir(parents=True)
        (u / 'experiences' / '2024-테스트경험.md').write_text(
            '# 테스트 경험\n\n## 메타\n\n- 카테고리: 리더십\n- 키워드: 리더십, 팀워크\n\n내용입니다', encoding='utf-8')
        (u / 'experiences' / '2023-다른경험.md').write_text(
            '# 다른 경험\n\n## 메타\n\n- 카테고리: 전문성·직무역량\n- 키워드: 데이터분석\n\n다른 내용입니다', encoding='utf-8')
        (u / 'profile.md').write_text('프로필', encoding='utf-8')
        (self.root / 'outputs' / '홍길동').mkdir(parents=True)
        app = create_app({'PROJECT_ROOT': self.root, 'TESTING': True})
        self.c = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, **kw):
        payload = {'company': '테스트기업', 'position': '테스트직무', 'question': '지원 동기', 'char_limit': 100}
        payload.update(kw)
        return self.c.post('/generate/홍길동', json=payload)

    def test_missing_char_limit_rejected_without_calling_llm(self):
        with patch.object(generate_route, 'stream_complete') as m:
            r = self.post(char_limit=None)
            body = r.get_data(as_text=True)
        self.assertIn('글자수', body)
        m.assert_not_called()

    def test_missing_question_rejected(self):
        with patch.object(generate_route, 'stream_complete') as m:
            r = self.post(question='')
            body = r.get_data(as_text=True)
        self.assertIn('문항이 없습니다', body)
        m.assert_not_called()

    def test_no_experiences_rejected(self):
        for f in (self.root / 'users' / '홍길동' / 'experiences').glob('*'):
            f.unlink()
        with patch.object(generate_route, 'stream_complete') as m:
            r = self.post()
            body = r.get_data(as_text=True)
        self.assertIn('경험 파일이 없습니다', body)
        m.assert_not_called()

    def test_within_5pct_no_retry(self):
        answer = '가' * 97  # 100자 목표, 오차 3% 이내 → 재시도 불필요
        fake = llm_result(f'===ANSWER===\n{answer}\n===END===\n===META===\n참조한 경험: 테스트 경험\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake) as m:
            r = self.post()
            body = r.get_data(as_text=True)
        self.assertEqual(m.call_count, 1)  # 재시도 없어야 함
        self.assertIn('"chars": 97', body)
        self.assertIn('"exceeded": false', body)
        saved = (self.root / 'outputs' / '홍길동' / '테스트기업_테스트직무' / '자소서.md').read_text(encoding='utf-8')
        self.assertIn(answer, saved)
        self.assertIn('97자', saved)

    def test_significant_undershoot_also_retries(self):
        """스킬 Step 9: "여유 있게 짧게 쓰는 것 금지" — 초과가 아니어도 많이 모자라면 다시 시킨다."""
        short = llm_result(f'===ANSWER===\n{"가" * 60}\n===END===\n===META===\n참조한 경험: x\n===END===\n')  # 100자 목표, 40% 미달
        better = llm_result(f'===ANSWER===\n{"가" * 95}\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', side_effect=[short, better]) as m:
            r = self.post()
            body = r.get_data(as_text=True)
        self.assertEqual(m.call_count, 2)
        self.assertIn('"stage": "retry"', body)
        self.assertIn('"chars": 95', body)
        self.assertIn('"exceeded": false', body)

    def test_over_limit_then_retry_succeeds(self):
        over = llm_result(f'===ANSWER===\n{"가" * 130}\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        fixed = llm_result(f'===ANSWER===\n{"가" * 90}\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', side_effect=[over, fixed]) as m:
            r = self.post()
            body = r.get_data(as_text=True)
        self.assertEqual(m.call_count, 2)
        self.assertIn('"stage": "retry"', body)
        self.assertIn('"chars": 90', body)
        self.assertIn('"exceeded": false', body)
        self.assertIn('"retries": 1', body)

    def test_still_over_after_retry_flagged_not_hidden(self):
        over1 = llm_result(f'===ANSWER===\n{"가" * 130}\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        over2 = llm_result(f'===ANSWER===\n{"가" * 120}\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', side_effect=[over1, over2]) as m:
            r = self.post()
            body = r.get_data(as_text=True)
        self.assertEqual(m.call_count, 2)  # MAX_RETRIES=1 이니 두 번째에서 멈춰야 함
        self.assertIn('"exceeded": true', body)
        saved = (self.root / 'outputs' / '홍길동' / '테스트기업_테스트직무' / '자소서.md').read_text(encoding='utf-8')
        self.assertIn('⚠️ 글자수 초과', saved)  # 저장은 하되 숨기지 않고 경고를 남긴다

    def test_unknown_override_file_rejected(self):
        """S11-2: 오타난 파일명이 섞이면 조용히 빼고 쓰지 말고 멈춰야 한다."""
        with patch.object(generate_route, 'stream_complete') as m:
            body = self.post(experience_files=['2024-테스트경험.md', '없는파일.md']).get_data(as_text=True)
        m.assert_not_called()
        self.assertIn('찾을 수 없습니다', body)
        self.assertIn('없는파일.md', body)

    def test_override_over_max_is_trimmed_and_flagged(self):
        """S11-2: UI 를 거치지 않고 직접 호출해도 상한이 걸려야 한다."""
        (self.root / 'users' / '홍길동' / 'experiences' / '2022-셋째경험.md').write_text(
            '# 셋째 경험\n\n## 메타\n\n- 카테고리: 리더십\n\n내용', encoding='utf-8')
        fake = llm_result('===ANSWER===\n' + '가' * 97 + '\n===END===\n===META===\nx\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake) as m:
            body = self.post(experience_files=[
                '2024-테스트경험.md', '2023-다른경험.md', '2022-셋째경험.md']).get_data(as_text=True)
        self.assertEqual(m.call_count, 1)
        self.assertIn('"stage": "overrides_trimmed"', body)
        self.assertIn('"dropped_overrides"', body)
        self.assertIn('"status": "review_required"', body)
        # 잘린 경험의 전문이 프롬프트에 실리면 안 된다
        sent = m.call_args.kwargs['messages'][0]['content']
        self.assertNotIn('셋째 경험', sent)

    def test_all_overrides_unknown_does_not_generate_with_zero_experiences(self):
        """S11-2: 전부 틀리면 selected 가 비는데, 예전엔 빈 선택 가드가 없어
        경험 0개짜리 프롬프트로 글을 썼다."""
        with patch.object(generate_route, 'stream_complete') as m:
            body = self.post(experience_files=['없는1.md', '없는2.md']).get_data(as_text=True)
        m.assert_not_called()
        self.assertIn('찾을 수 없습니다', body)

    def test_over_limit_marks_review_required(self):
        """S11-4: 저장은 하되 ready 로 올리지 않는다. 기존 test_still_over_after_retry_flagged_not_hidden
        이 보장하는 "숨기지 않는다" 에 더해, 성공으로 표시하지도 않는지를 본다."""
        over1 = llm_result(f'===ANSWER===\n{"가" * 130}\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        over2 = llm_result(f'===ANSWER===\n{"가" * 120}\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', side_effect=[over1, over2]):
            body = self.post().get_data(as_text=True)
        self.assertIn('"status": "review_required"', body)
        self.assertIn('"over_limit"', body)

    def test_ready_for_user_when_within_limit(self):
        answer = '가' * 97
        fake = llm_result(f'===ANSWER===\n{answer}\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake):
            body = self.post().get_data(as_text=True)
        self.assertIn('"status": "ready_for_user"', body)
        self.assertIn('"issues": []', body)

    def test_rel_path_keeps_subfolder(self):
        """S11-5: 예전엔 화면이 saved_to 에서 파일명만 떼어 기업_직무/ 를 잃어 링크가 깨졌다.
        rel_path 는 output_dir 기준이라 /output/<user>/ 뒤에 그대로 붙일 수 있어야 한다."""
        fake = llm_result('===ANSWER===\n' + '가' * 97 + '\n===END===\n===META===\nx\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake):
            body = self.post().get_data(as_text=True)
        self.assertIn('"rel_path": "테스트기업_테스트직무/자소서.md"', body)

    def test_count_mode_excludes_whitespace(self):
        answer = ('가 ' * 69).strip()  # 공백 제외 69자 → 70자 목표 대비 오차 1.4%, 재시도 불필요
        fake = llm_result(f'===ANSWER===\n{answer}\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake) as m:
            r = self.post(char_limit=70, count_mode='제외')
            body = r.get_data(as_text=True)
        self.assertEqual(m.call_count, 1)
        self.assertIn('"exceeded": false', body)

    def test_appends_to_existing_output_file(self):
        out = self.root / 'outputs' / '홍길동' / '테스트기업_테스트직무' / '자소서.md'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('# 테스트기업 테스트직무\n\n## 기존 문항\n기존 내용\n', encoding='utf-8')
        fake = llm_result('===ANSWER===\n새 답변\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake):
            r = self.post(question='새 문항')
            r.get_data()  # 스트리밍 응답은 지연 평가되므로 patch 블록 안에서 소비해야 실제로 실행된다
        text = out.read_text(encoding='utf-8')
        self.assertIn('기존 내용', text)
        self.assertIn('새 답변', text)

    def test_experience_files_override_changes_prompt_content(self):
        """S3-1 완료 조건: 같은 문항에 경험 A, B 를 각각 지정하면 프롬프트에 실리는 내용이 달라진다."""
        fake = llm_result('===ANSWER===\n답변\n===END===\n===META===\n참조한 경험: x\n===END===\n')

        with patch.object(generate_route, 'stream_complete', return_value=fake) as m:
            r = self.post(experience_files=['2024-테스트경험.md'])
            r.get_data()
        prompt_a = m.call_args.kwargs['messages'][0]['content']
        self.assertIn('2024-테스트경험.md', prompt_a)
        self.assertIn('내용입니다', prompt_a)
        self.assertNotIn('다른 내용입니다', prompt_a)

        with patch.object(generate_route, 'stream_complete', return_value=fake) as m:
            r = self.post(experience_files=['2023-다른경험.md'])
            r.get_data()
        prompt_b = m.call_args.kwargs['messages'][0]['content']
        self.assertIn('2023-다른경험.md', prompt_b)
        self.assertIn('다른 내용입니다', prompt_b)
        self.assertNotIn('테스트 경험\n\n## 메타', prompt_b)  # 첫 번째 경험 파일 전문은 없어야 함

        self.assertNotEqual(prompt_a, prompt_b)

    def test_experience_files_override_as_comma_string(self):
        """폼 인코딩으로 오면 콤마 구분 문자열일 수 있다."""
        fake = llm_result('===ANSWER===\n답변\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake) as m:
            self.post(experience_files='2023-다른경험.md').get_data()
        prompt = m.call_args.kwargs['messages'][0]['content']
        self.assertIn('다른 내용입니다', prompt)

    def test_trends_document_included_in_prompt_when_present(self):
        """고도화(2026-09-27): lib/trends.py 가 조사해둔 트렌드가 자소서 프롬프트에 실린다."""
        trends_dir = self.root / 'references' / 'trends'
        trends_dir.mkdir(parents=True)
        (trends_dir / 'coverletter-trends.md').write_text(
            '# 자소서 작성 트렌드\n\n## 구조\n두괄식 선호', encoding='utf-8')
        fake = llm_result('===ANSWER===\n답변\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake) as m:
            self.post().get_data()
        prompt = m.call_args.kwargs['messages'][0]['content']
        self.assertIn('두괄식 선호', prompt)
        # writing-style.md 가 우선한다는 지시가 트렌드 참고 바로 옆에 있어야 한다
        self.assertIn('writing-style.md', prompt.split('두괄식 선호')[0][-300:])

    def test_missing_trends_document_does_not_break_prompt(self):
        fake = llm_result('===ANSWER===\n답변\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake) as m:
            r = self.post()
            r.get_data()
        prompt = m.call_args.kwargs['messages'][0]['content']
        self.assertIn('아직 조사된 트렌드 없음', prompt)

    def test_use_trends_false_excludes_trends_from_prompt(self):
        """고도화(2026-09-28): 대시보드 '최신 트렌드 반영' 토글을 끄면 트렌드 문서를 아예 안 보낸다."""
        trends_dir = self.root / 'references' / 'trends'
        trends_dir.mkdir(parents=True)
        (trends_dir / 'coverletter-trends.md').write_text(
            '# 자소서 작성 트렌드\n\n## 구조\n두괄식 선호', encoding='utf-8')
        fake = llm_result('===ANSWER===\n답변\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake) as m:
            self.post(use_trends=False).get_data()
        prompt = m.call_args.kwargs['messages'][0]['content']
        self.assertNotIn('두괄식 선호', prompt)
        self.assertIn('아직 조사된 트렌드 없음', prompt)

    def test_use_trends_defaults_true_when_omitted(self):
        """토글 값을 아예 안 보내는 기존 호출(폼 없이)도 트렌드 반영 기본값(True)을 유지해야 한다."""
        trends_dir = self.root / 'references' / 'trends'
        trends_dir.mkdir(parents=True)
        (trends_dir / 'coverletter-trends.md').write_text(
            '# 자소서 작성 트렌드\n\n## 구조\n두괄식 선호', encoding='utf-8')
        fake = llm_result('===ANSWER===\n답변\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake) as m:
            self.post().get_data()
        prompt = m.call_args.kwargs['messages'][0]['content']
        self.assertIn('두괄식 선호', prompt)

    def test_selected_event_reports_question_type_and_reasons(self):
        fake = llm_result('===ANSWER===\n답변\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake):
            r = self.post(question='리더십을 발휘한 경험을 서술하시오')
            body = r.get_data(as_text=True)
        self.assertIn('"stage": "selected"', body)
        self.assertIn('"question_type": "경험형"', body)
        self.assertIn('2024-테스트경험.md', body)  # 리더십 키워드가 겹치는 경험이 선택되어야 함

    def test_done_event_reports_used_experiences_from_code_not_llm(self):
        fake = llm_result('===ANSWER===\n답변\n===END===\n===META===\n참조한 경험: 아무거나\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake):
            r = self.post(experience_files=['2023-다른경험.md'])
            body = r.get_data(as_text=True)
        self.assertIn('"used_experiences": ["2023-다른경험.md"]', body)

    def test_unsafe_company_or_position_rejected_before_llm_call(self):
        with patch.object(generate_route, 'stream_complete') as m:
            r = self.post(company='..', position='x')
            body = r.get_data(as_text=True)
        self.assertIn('기업명', body)
        m.assert_not_called()

        with patch.object(generate_route, 'stream_complete') as m:
            r = self.post(company='x', position='../../etc')
            body = r.get_data(as_text=True)
        self.assertIn('직무명', body)
        m.assert_not_called()


class GenerateBundleTest(unittest.TestCase):
    """S7-3: 공고 하나를 고르면 이력서·자소서·포트폴리오를 한 번에 생성한다."""

    def setUp(self):
        from lib import db
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        self.db_path = self.tmp / 'app.db'
        u = self.root / 'users' / '홍길동'
        (u / 'experiences').mkdir(parents=True)
        (u / 'experiences' / '2024-테스트경험.md').write_text(
            '# 테스트 경험\n\n## 메타\n\n- 카테고리: 리더십\n\n### Result (결과)\n\n성과를 냈다.',
            encoding='utf-8')
        (self.root / 'outputs' / '홍길동').mkdir(parents=True)
        db.init_db(self.db_path)
        self.db = db
        conn = db.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json) "
            "VALUES ('manual','0001','테스트기업','백엔드','[\"조건1\"]','[]')"
        )
        self.job_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        conn.commit()
        conn.close()
        app = create_app({'PROJECT_ROOT': self.root, 'DB_PATH': self.db_path, 'TESTING': True})
        self.c = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def insert_match(self):
        conn = self.db.get_conn(self.db_path)
        conn.execute(
            'INSERT INTO matches (user, job_id, score, required_hit, preferred_hit, evidence_json, missing_json) '
            "VALUES ('홍길동', ?, 100, 1.0, 0, ?, '[]')",
            (self.job_id, '[{"condition": "조건1", "kind": "필수", "verdict": "충족", '
                           '"evidence_file": "2024-테스트경험.md", "note": ""}]'),
        )
        conn.commit()
        conn.close()

    def post(self, **overrides):
        payload = {'job_id': str(self.job_id), 'question': '지원 동기', 'char_limit': '50'}
        payload.update(overrides)
        return self.c.post('/generate/홍길동/bundle', json=payload)

    def test_over_limit_coverletter_is_not_ok(self):
        """S11-4: 번들은 done 만 보고 coverletter_ok=True 로 올렸다. 글자수를 초과한
        초안은 저장되더라도 성공이 아니다."""
        self.insert_match()
        over = llm_result('===ANSWER===\n' + '가' * 130 + '\n===END===\n===META===\nx\n===END===\n')
        fake_portfolio = llm_result('===TOC===\n1. 도입\n===END===\n\n===CHAPTER: 1===\n요약\n===END===\n')
        with patch.object(portfolio_module, 'stream_complete', return_value=fake_portfolio), \
             patch.object(generate_route, 'stream_complete', return_value=over):
            body = self.post().get_data(as_text=True)
        self.assertIn('"coverletter_ok": false', body)
        self.assertIn('"coverletter_status": "review_required"', body)
        self.assertIn('"over_limit"', body)

    def test_within_limit_coverletter_is_ok(self):
        self.insert_match()
        fine = llm_result('===ANSWER===\n' + '가' * 49 + '\n===END===\n===META===\nx\n===END===\n')
        fake_portfolio = llm_result('===TOC===\n1. 도입\n===END===\n\n===CHAPTER: 1===\n요약\n===END===\n')
        with patch.object(portfolio_module, 'stream_complete', return_value=fake_portfolio), \
             patch.object(generate_route, 'stream_complete', return_value=fine):
            body = self.post().get_data(as_text=True)
        self.assertIn('"coverletter_ok": true', body)
        self.assertIn('"coverletter_status": "ready_for_user"', body)

    def test_missing_job_id_rejected(self):
        r = self.c.post('/generate/홍길동/bundle', json={'question': 'x', 'char_limit': '50'})
        self.assertEqual(r.status_code, 400)

    def test_unknown_job_id_errors_via_sse(self):
        with patch.object(generate_route.matcher, 'match_job') as mm:
            r = self.post(job_id='9999')
            body = r.get_data(as_text=True)
        mm.assert_not_called()
        self.assertIn('공고를 찾을 수 없습니다', body)

    def test_runs_matching_when_no_prior_match(self):
        # portfolio 도 이 호출 안에서 돌아간다 — lib.portfolio 는 stream_complete 를 자기
        # 이름공간으로 따로 import 했으므로 generate_route 쪽 patch 만으론 안 막힌다(실제로
        # 처음엔 이걸 빠뜨려서 이 테스트가 real API 를 부를 뻔했다).
        fake_answer = llm_result('===ANSWER===\n답변입니다\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        fake_portfolio = llm_result('===TOC===\n1. 도입\n===END===\n\n===CHAPTER: 1===\n요약\n===END===\n')
        with patch.object(generate_route.matcher, 'match_job') as mm, \
             patch.object(portfolio_module, 'stream_complete', return_value=fake_portfolio), \
             patch.object(generate_route, 'stream_complete', return_value=fake_answer):
            mm.return_value = generate_route.matcher.MatchResult(
                ok=True, job_id=self.job_id, user='홍길동', company='테스트기업', position='백엔드',
                score=90.0, required_hit=1.0, preferred_hit=0.0, category='적합')
            r = self.post()
            body = r.get_data(as_text=True)
        mm.assert_called_once()
        self.assertIn('"doc": "match"', body)

    def test_skips_matching_when_match_already_exists(self):
        self.insert_match()
        fake_answer = llm_result('===ANSWER===\n답변입니다\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        fake_portfolio = llm_result('===TOC===\n1. 도입\n===END===\n\n===CHAPTER: 1===\n요약\n===END===\n')
        with patch.object(generate_route.matcher, 'match_job') as mm, \
             patch.object(portfolio_module, 'stream_complete', return_value=fake_portfolio), \
             patch.object(generate_route, 'stream_complete', return_value=fake_answer):
            r = self.post()
            body = r.get_data(as_text=True)
        mm.assert_not_called()
        self.assertNotIn('"doc": "match"', body)

    def test_all_three_documents_saved_to_same_folder(self):
        # 주의: 자소서(generate_route)와 포트폴리오(lib.portfolio)는 각자 lib.llm.stream_complete 를
        # 자기 모듈 이름공간으로 따로 import 했다 — generate_route 쪽만 patch 하면 포트폴리오 호출은
        # 실제 API 로 새어나간다(이 테스트를 처음 짤 때 실제로 걸릴 뻔한 함정). 둘 다 따로 patch 한다.
        self.insert_match()
        fake_answer = llm_result('===ANSWER===\n답변입니다\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        fake_portfolio = llm_result(
            '===TOC===\n1. 도입\n===END===\n\n===CHAPTER: 1===\n요약\n===END===\n'
        )
        with patch.object(portfolio_module, 'stream_complete', return_value=fake_portfolio), \
             patch.object(generate_route, 'stream_complete', return_value=fake_answer):
            r = self.post()
            body = r.get_data(as_text=True)

        self.assertIn('"doc": "resume"', body)
        self.assertIn('"doc": "portfolio"', body)
        self.assertIn('"doc": "coverletter"', body)
        self.assertIn('event: done', body)

        folder = self.root / 'outputs' / '홍길동' / '테스트기업_백엔드'
        self.assertTrue((folder / '이력서.md').exists())
        self.assertTrue((folder / 'portfolio-outline.md').exists())
        self.assertTrue((folder / '자소서.md').exists())

    def test_unsafe_company_name_sanitized_for_folder(self):
        conn = self.db.get_conn(self.db_path)
        conn.execute(
            "INSERT INTO jobs (source, source_job_id, company, position, required_json, preferred_json) "
            "VALUES ('manual','0002','../../evil','백엔드','[]','[]')"
        )
        job_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        conn.execute(
            'INSERT INTO matches (user, job_id, score, required_hit, preferred_hit, evidence_json, missing_json) '
            "VALUES ('홍길동', ?, 0, 0, 0, '[]', '[]')", (job_id,),
        )
        conn.commit()
        conn.close()

        fake_answer = llm_result('===ANSWER===\n답변\n===END===\n===META===\nx\n===END===\n')
        fake_portfolio = llm_result('===TOC===\n1. 도입\n===END===\n\n===CHAPTER: 1===\n요약\n===END===\n')
        with patch.object(portfolio_module, 'stream_complete', return_value=fake_portfolio), \
             patch.object(generate_route, 'stream_complete', return_value=fake_answer):
            r = self.post(job_id=str(job_id))
            r.get_data()
        self.assertFalse((self.tmp / 'evil_백엔드').exists())
        self.assertFalse((self.root / 'outputs' / '..' / 'evil_백엔드').resolve().exists())

    def test_does_not_touch_real_project_db(self):
        """DB_PATH 격리 회귀 방지: 이 테스트 자체가 실제 data/app.db 를 안 건드리는지 보장한다."""
        self.insert_match()
        fake_portfolio = llm_result('===TOC===\n1. 도입\n===END===\n\n===CHAPTER: 1===\n요약\n===END===\n')
        with patch.object(portfolio_module, 'stream_complete', return_value=fake_portfolio), \
             patch.object(generate_route, 'stream_complete',
                           return_value=llm_result('===ANSWER===\nx\n===END===\n===META===\nx\n===END===\n')):
            self.post().get_data()
        real_conn = self.db.get_conn()  # db_path 생략 → 실제 프로젝트 DB
        try:
            count = real_conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE company = '테스트기업'").fetchone()['n']
        finally:
            real_conn.close()
        self.assertEqual(count, 0)


if __name__ == '__main__':
    unittest.main()
