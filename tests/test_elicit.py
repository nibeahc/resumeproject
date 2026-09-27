"""S3-4 대화형 발굴 라우트 테스트. LLM 은 스텁, 저장/파싱/경로안전성은 실제로 검증한다."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web import create_app  # noqa: E402
from web.routes import elicit as elicit_route  # noqa: E402


def llm_result(text, stop_reason='end_turn'):
    return {'text': text, 'stop_reason': stop_reason,
            'usage': {'input_tokens': 1, 'output_tokens': 1, 'thinking_tokens': 0}}


class ElicitRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        (self.root / 'users' / '홍길동').mkdir(parents=True)
        app = create_app({'PROJECT_ROOT': self.root, 'TESTING': True})
        self.c = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_page_renders_for_existing_user(self):
        r = self.c.get('/elicit/홍길동')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'chatBox', r.data)

    def test_unknown_user_redirects_home(self):
        r = self.c.get('/elicit/없는사람')
        self.assertEqual(r.status_code, 302)

    def test_first_call_with_no_messages_sends_start_signal(self):
        # stream_complete 에 넘어온 messages 는 라우트가 반환 직후 같은 리스트 객체에 append 로
        # 이어붙인다. Mock.call_args 는 참조만 저장하므로, 나중에 조사하면 이미 바뀐 뒤다.
        # side_effect 로 호출 "그 순간"의 내용을 복사해 둔다.
        captured = []

        def fake(system, messages, **kw):
            captured.append([dict(m) for m in messages])
            return llm_result('안녕하세요! 시작해볼게요.')

        with patch.object(elicit_route, 'stream_complete', side_effect=fake):
            r = self.c.post('/elicit/홍길동', json={'messages': [], 'message': ''})
        self.assertEqual(captured[0][-1]['content'], '(대화를 시작해주세요)')
        data = r.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['reply'], '안녕하세요! 시작해볼게요.')

    def test_user_message_appended_and_sent(self):
        with patch.object(elicit_route, 'stream_complete', return_value=llm_result('네, 알겠습니다.')):
            r = self.c.post('/elicit/홍길동', json={
                'messages': [{'role': 'user', 'content': '(대화를 시작해주세요)'},
                             {'role': 'assistant', 'content': '어느 시기 활동인가요?'}],
                'message': '최근 1년이요',
            })
        data = r.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['messages'][-2], {'role': 'user', 'content': '최근 1년이요'})
        self.assertEqual(data['messages'][-1], {'role': 'assistant', 'content': '네, 알겠습니다.'})

    def test_ready_block_saves_experience_and_hides_block_from_chat(self):
        body = "# 교내 동아리 프로젝트\n\n## 메타\n\n- 카테고리: 리더십\n- 키워드: 리더십\n\n내용"
        reply = f"좋습니다, 정리했어요.\n\n===EXPERIENCE_READY===\n파일명: 2024-교내동아리프로젝트.md\n\n{body}\n===END==="
        with patch.object(elicit_route, 'stream_complete', return_value=llm_result(reply)):
            r = self.c.post('/elicit/홍길동', json={'messages': [], 'message': '네 저장해주세요'})
        data = r.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['saved']['filename'], '2024-교내동아리프로젝트.md')
        self.assertEqual(data['saved']['title'], '교내 동아리 프로젝트')
        self.assertNotIn('===EXPERIENCE_READY===', data['reply'])  # 기계용 블록은 화면에 안 보여야 함
        self.assertIn('저장 완료', data['reply'])

        saved_path = self.root / 'users' / '홍길동' / 'experiences' / '2024-교내동아리프로젝트.md'
        self.assertTrue(saved_path.exists())
        self.assertIn('# 교내 동아리 프로젝트', saved_path.read_text(encoding='utf-8'))

    def test_ready_block_without_approval_text_is_not_special(self):
        """블록이 없으면 그냥 평범한 대화 응답으로 취급된다 (저장 없음)."""
        with patch.object(elicit_route, 'stream_complete', return_value=llm_result('행동을 좀 더 구체적으로 말씀해주시겠어요?')):
            r = self.c.post('/elicit/홍길동', json={'messages': [], 'message': '동아리 활동했어요'})
        data = r.get_json()
        self.assertIsNone(data['saved'])

    def test_unsafe_filename_in_ready_block_not_saved(self):
        reply = "===EXPERIENCE_READY===\n파일명: ../../../etc/passwd.md\n\n# 탈취\n===END==="
        with patch.object(elicit_route, 'stream_complete', return_value=llm_result(reply)):
            r = self.c.post('/elicit/홍길동', json={'messages': [], 'message': '저장'})
        data = r.get_json()
        self.assertIsNone(data['saved'])
        self.assertFalse((self.tmp / 'etc' / 'passwd.md').exists())

    def test_no_message_and_nonempty_history_without_new_message_errors(self):
        with patch.object(elicit_route, 'stream_complete') as m:
            r = self.c.post('/elicit/홍길동', json={
                'messages': [{'role': 'user', 'content': 'x'}, {'role': 'assistant', 'content': 'y'}],
                'message': '',
            })
        self.assertEqual(r.status_code, 400)
        m.assert_not_called()

    def test_too_long_history_rejected(self):
        long_history = [{'role': 'user', 'content': 'x'}] * 100
        with patch.object(elicit_route, 'stream_complete') as m:
            r = self.c.post('/elicit/홍길동', json={'messages': long_history, 'message': '계속'})
        self.assertEqual(r.status_code, 400)
        m.assert_not_called()

    def test_malformed_history_entries_are_dropped_not_crashed(self):
        with patch.object(elicit_route, 'stream_complete', return_value=llm_result('네')):
            r = self.c.post('/elicit/홍길동', json={
                'messages': [{'role': 'system', 'content': 'x'}, 'not-a-dict', {'role': 'user'}],
                'message': '안녕하세요',
            })
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['success'])


class ElicitEnrichmentModeTest(unittest.TestCase):
    """고도화(2026-09-27, 사용자 요청): "⚠️ 표시된 것들 대화로 내용 추가할 수 있으면 좋겠다".
    새 경험을 만드는 게 아니라 기존 경험 하나를 보강하는 모드 — 자동 저장 없이 항상 같은
    파일명으로 draft 만 돌려주고, 실제 저장은 별도의 /save 호출이 한다."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        exp_dir = self.root / 'users' / '홍길동' / 'experiences'
        exp_dir.mkdir(parents=True)
        self.existing = ('# 학회 발표\n\n## 상세 내용 (STAR)\n\n### Result (결과)\n\n'
                          '> ⚠️ STAR 보강 필요: 수치가 없습니다.\n')
        (exp_dir / '2024-학회발표.md').write_text(self.existing, encoding='utf-8')
        app = create_app({'PROJECT_ROOT': self.root, 'TESTING': True})
        self.c = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_page_shows_edit_context_for_existing_file(self):
        r = self.c.get('/elicit/홍길동?file=2024-학회발표.md')
        self.assertEqual(r.status_code, 200)
        body = r.data.decode('utf-8')
        self.assertIn('학회 발표', body)
        self.assertIn('2024-학회발표.md', body)
        # 본문 미리보기 초기값(currentDraft)에 기존 파일 내용이 그대로 들어가 있어야 한다
        self.assertIn('STAR', body)
        self.assertIn('previewBody', body)

    def test_page_redirects_when_file_missing(self):
        r = self.c.get('/elicit/홍길동?file=없는파일.md')
        self.assertEqual(r.status_code, 302)

    def test_page_redirects_on_traversal_attempt(self):
        r = self.c.get('/elicit/홍길동?file=../../../etc/passwd')
        self.assertEqual(r.status_code, 302)

    def test_turn_sends_existing_content_in_system_prompt(self):
        captured = {}

        def fake(system, messages, **kw):
            captured['system'] = system
            return llm_result('어떤 수치가 있었나요?')

        with patch.object(elicit_route, 'stream_complete', side_effect=fake):
            r = self.c.post('/elicit/홍길동', json={
                'messages': [], 'message': '', 'edit_filename': '2024-학회발표.md',
            })
        self.assertTrue(r.get_json()['success'])
        self.assertIn('STAR 보강 필요', captured['system'])
        self.assertIn('학회 발표', captured['system'])
        self.assertIn('파일명은 절대 바꾸지 않습니다', captured['system'])

    def test_ready_block_returns_draft_without_writing_to_disk(self):
        """고도화(2026-09-27, 사용자 요청): 보강 모드는 더 이상 자동 저장하지 않는다 —
        미리보기용 draft 만 돌려주고, 실제 저장은 상세 페이지의 "저장" 버튼(별도의
        /experience/.../save 호출)이 한다. 모델이 다른 파일명을 써도(실수로) 무시하고
        draft.filename 은 항상 기존 파일명 그대로다."""
        reply = ("좋습니다.\n\n===EXPERIENCE_READY===\n파일명: 아무거나.md\n\n"
                 "# 학회 발표\n\n## 상세 내용 (STAR)\n\n### Result (결과)\n\n참가자 30명 설문 응답률 90%\n"
                 "\n===END===")
        with patch.object(elicit_route, 'stream_complete', return_value=llm_result(reply)):
            r = self.c.post('/elicit/홍길동', json={
                'messages': [], 'message': '참가자 30명, 응답률 90%였어요',
                'edit_filename': '2024-학회발표.md',
            })
        data = r.get_json()
        self.assertTrue(data['success'])
        self.assertIsNone(data['saved'])
        self.assertEqual(data['draft']['filename'], '2024-학회발표.md')
        self.assertIn('응답률 90%', data['draft']['content'])
        self.assertIn('저장', data['reply'])
        # 아직 저장 버튼을 누르지 않았으므로 원본 파일은 그대로여야 한다
        exp_path = self.root / 'users' / '홍길동' / 'experiences' / '2024-학회발표.md'
        self.assertEqual(exp_path.read_text(encoding='utf-8'), self.existing)
        # "아무거나.md" 라는 새 파일이 따로 생기면 안 된다
        self.assertFalse((self.root / 'users' / '홍길동' / 'experiences' / '아무거나.md').exists())

    def test_conversation_continues_after_draft_update(self):
        """사용자 지적(2026-09-27): "한 곳 고치면 대화가 끝나는 게 아니라 계속 이어졌으면
        좋겠다" — 블록 앞뒤로 요약과 다음 질문이 같이 와도 둘 다 화면에 남아야 한다."""
        reply = ("이렇게 이해했습니다: 네덜란드 교환학생 중 현지 고등학생 대상.\n\n"
                 "다음으로, 이 활동에서 구체적으로 어떤 역할을 맡으셨나요?\n\n"
                 "===EXPERIENCE_READY===\n파일명: 아무거나.md\n\n# 학회 발표\n\n내용\n\n===END===")
        with patch.object(elicit_route, 'stream_complete', return_value=llm_result(reply)):
            r = self.c.post('/elicit/홍길동', json={
                'messages': [{'role': 'user', 'content': 'x'}, {'role': 'assistant', 'content': 'y'}],
                'message': '네덜란드 교환학생 중 현지 고등학생 대상으로 했어요',
                'edit_filename': '2024-학회발표.md',
            })
        data = r.get_json()
        self.assertTrue(data['success'])
        self.assertIn('구체적으로 어떤 역할', data['reply'])  # 다음 질문이 안 사라짐
        self.assertNotIn('EXPERIENCE_READY', data['reply'])   # 기계용 블록만 화면에서 제거됨
        self.assertIsNotNone(data['draft'])

    def test_save_reminder_shown_only_on_first_turn(self):
        """사용자 지적: "저장 버튼 안내는 시작하면서 한 번만, 그 뒤로는 안 했으면 해"."""
        with patch.object(elicit_route, 'stream_complete', return_value=llm_result('어느 시기 활동인가요?')):
            r1 = self.c.post('/elicit/홍길동', json={
                'messages': [], 'message': '', 'edit_filename': '2024-학회발표.md',
            })
        self.assertIn('저장', r1.get_json()['reply'])

        with patch.object(elicit_route, 'stream_complete', return_value=llm_result('알겠습니다, 다음 질문입니다.')):
            r2 = self.c.post('/elicit/홍길동', json={
                'messages': r1.get_json()['messages'], 'message': '2023년이요',
                'edit_filename': '2024-학회발표.md',
            })
        self.assertNotIn('저장', r2.get_json()['reply'])

    def test_truncated_reply_does_not_leak_raw_block_to_chat(self):
        """실측(2026-09-27)으로 발견한 버그의 회귀 테스트: max_tokens=1500 이던 시절 보강
        모드는 매 턴 문서 전체를 다시 출력해야 해서 자꾸 잘렸고, 잘리면 ===END=== 이 없어
        _READY_BLOCK 이 매치를 못 해 반쯤 나온 기계용 블록이 그대로 채팅에 노출됐다(사용자가
        실제 대화 로그로 지적). stop_reason 이 max_tokens 이고 블록이 미완성이면 깨진 원문
        대신 안내 문구를 보여줘야 한다."""
        truncated = ("좋습니다, 정리하겠습니다.\n\n===EXPERIENCE_READY===\n"
                     "파일명: 2024-학회발표.md\n\n# 학회 발표\n\n## 상세 내용 (STAR)\n\n"
                     "### Result (결과)\n\n참가자 40명 대상 설문에서 만족도 92%를 기록했으며 이는")
        with patch.object(elicit_route, 'stream_complete',
                          return_value=llm_result(truncated, stop_reason='max_tokens')):
            r = self.c.post('/elicit/홍길동', json={
                'messages': [], 'message': '참가자 40명, 만족도 92%였어요',
                'edit_filename': '2024-학회발표.md',
            })
        data = r.get_json()
        self.assertTrue(data['success'])
        self.assertIsNone(data['draft'])
        self.assertIsNone(data['saved'])
        self.assertNotIn('EXPERIENCE_READY', data['reply'])  # 깨진 원문이 새어나가면 안 됨
        self.assertNotIn('파일명:', data['reply'])
        self.assertIn('잘렸습니다', data['reply'])

    def test_missing_edit_file_returns_404(self):
        with patch.object(elicit_route, 'stream_complete') as m:
            r = self.c.post('/elicit/홍길동', json={
                'messages': [], 'message': '', 'edit_filename': '없는파일.md',
            })
        self.assertEqual(r.status_code, 404)
        m.assert_not_called()

    def test_invalid_edit_filename_rejected(self):
        with patch.object(elicit_route, 'stream_complete') as m:
            r = self.c.post('/elicit/홍길동', json={
                'messages': [], 'message': '', 'edit_filename': '../../etc/passwd',
            })
        self.assertEqual(r.status_code, 400)
        m.assert_not_called()


if __name__ == '__main__':
    unittest.main()
