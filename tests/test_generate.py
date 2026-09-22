"""S1-3 생성 라우트 테스트. 핵심은 글자수를 코드로 검증하는 부분이다 (LLM 을 믿지 않는다)."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


class GeneratePageTest(unittest.TestCase):
    """S1-4: GET /generate/<name> 페이지 렌더링."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        (self.root / 'users' / '홍길동').mkdir(parents=True)
        app = create_app({'PROJECT_ROOT': self.root, 'TESTING': True})
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
        saved = (self.root / 'outputs' / '홍길동' / '테스트기업_테스트직무.md').read_text(encoding='utf-8')
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
        saved = (self.root / 'outputs' / '홍길동' / '테스트기업_테스트직무.md').read_text(encoding='utf-8')
        self.assertIn('⚠️ 글자수 초과', saved)  # 저장은 하되 숨기지 않고 경고를 남긴다

    def test_count_mode_excludes_whitespace(self):
        answer = ('가 ' * 69).strip()  # 공백 제외 69자 → 70자 목표 대비 오차 1.4%, 재시도 불필요
        fake = llm_result(f'===ANSWER===\n{answer}\n===END===\n===META===\n참조한 경험: x\n===END===\n')
        with patch.object(generate_route, 'stream_complete', return_value=fake) as m:
            r = self.post(char_limit=70, count_mode='제외')
            body = r.get_data(as_text=True)
        self.assertEqual(m.call_count, 1)
        self.assertIn('"exceeded": false', body)

    def test_appends_to_existing_output_file(self):
        out = self.root / 'outputs' / '홍길동' / '테스트기업_테스트직무.md'
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


if __name__ == '__main__':
    unittest.main()
