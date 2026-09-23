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


def llm_result(text):
    return {'text': text, 'stop_reason': 'end_turn',
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


if __name__ == '__main__':
    unittest.main()
