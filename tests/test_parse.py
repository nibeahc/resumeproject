"""S1-2 파싱 라우트 테스트. LLM 호출은 스텁으로, 나머지는 실제로 검증한다.

텍스트 추출 자체(성공/실패 각 케이스)는 lib/extract.py 소관이라 tests/test_extract.py 가 다룬다.
여기서는 parse.py 의 extract_text() 가 그 모듈을 제대로 호출해서 결과를 (ok, text, note) 튜플로
바꿔주는지만 얇게 확인한다."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web import create_app  # noqa: E402
from web.routes import parse as parse_route  # noqa: E402


class ExtractTextWrapperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_delegates_to_lib_extract_on_success(self):
        p = self.tmp / 'a.md'
        p.write_text('가' * 150, encoding='utf-8')
        ok, text, note = parse_route.extract_text(p)
        self.assertTrue(ok)
        self.assertEqual(len(text), 150)
        self.assertEqual(note, '')

    def test_delegates_to_lib_extract_on_failure(self):
        p = self.tmp / 'a.txt'
        p.write_text('짧음', encoding='utf-8')
        ok, text, note = parse_route.extract_text(p)
        self.assertFalse(ok)
        self.assertEqual(text, '')
        self.assertIn('100자 미만', note)


class ParseResultPathSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.project_root = self.tmp.resolve()
        self.root = (self.project_root / 'users' / '홍길동')
        self.root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_files_inside_user_folder(self):
        text = (
            '===FILE: users/홍길동/writing-style.md===\n내용A\n===END===\n'
            '===FILE: users/홍길동/experiences/2024-테스트.md===\n내용B\n===END===\n'
            '===REPORT===\n완료 보고 본문\n===END===\n'
        )
        written, rejected, report = parse_route.parse_result(text, self.project_root, self.root)
        self.assertEqual(len(written), 2)
        self.assertEqual(rejected, [])
        self.assertEqual((self.root / 'writing-style.md').read_text(encoding='utf-8').strip(), '내용A')
        self.assertEqual((self.root / 'experiences' / '2024-테스트.md').read_text(encoding='utf-8').strip(), '내용B')
        self.assertEqual(report, '완료 보고 본문')

    def test_path_outside_user_folder_rejected_and_not_written(self):
        outside = self.project_root / 'users' / '다른사람' / 'profile.md'
        text = (
            '===FILE: users/다른사람/profile.md===\n탈취\n===END===\n'
            '===FILE: ../../../etc/passwd===\n탈취2\n===END===\n'
        )
        written, rejected, report = parse_route.parse_result(text, self.project_root, self.root)
        self.assertEqual(written, [])
        self.assertEqual(len(rejected), 2)
        self.assertFalse(outside.exists())

    def test_no_file_blocks_returns_empty(self):
        written, rejected, report = parse_route.parse_result('아무 형식도 아닌 텍스트', self.project_root, self.root)
        self.assertEqual((written, rejected, report), ([], [], ''))


class ParseRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'root'
        (self.root / 'users' / '홍길동' / 'raw' / 'apply').mkdir(parents=True)
        (self.root / 'users' / '홍길동' / 'raw' / 'logs').mkdir(parents=True)
        (self.root / 'users' / '홍길동' / 'experiences').mkdir(parents=True)
        app = create_app({'PROJECT_ROOT': self.root, 'TESTING': True})
        self.c = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _events(self, resp):
        body = resp.get_data(as_text=True)
        return [b for b in body.split('\n\n') if b.strip()]

    def test_no_raw_files_returns_error_event(self):
        r = self.c.post('/parse/홍길동')
        self.assertIn('raw/ 폴더에 자소서 파일이 없습니다', r.get_data(as_text=True))

    def test_existing_experiences_without_force_rejected(self):
        (self.root / 'users' / '홍길동' / 'experiences' / 'a.md').write_text('x', encoding='utf-8')
        (self.root / 'users' / '홍길동' / 'raw' / 'apply' / 'a.md').write_text('나' * 150, encoding='utf-8')
        r = self.c.post('/parse/홍길동')
        body = r.get_data(as_text=True)
        self.assertIn('이미 파싱된 경험이 1개', body)
        self.assertNotIn('llm_start', body)  # LLM 을 호출하지 않고 바로 거부해야 한다

    def test_full_flow_with_stubbed_llm(self):
        (self.root / 'users' / '홍길동' / 'raw' / 'apply' / 'a.md').write_text('다' * 150, encoding='utf-8')
        fake_text = (
            '===FILE: users/홍길동/writing-style.md===\n스타일\n===END===\n'
            '===REPORT===\n파싱 완료: 홍길동\n===END===\n'
        )
        with patch.object(parse_route, 'stream_complete',
                           return_value={'text': fake_text, 'stop_reason': 'end_turn',
                                         'usage': {'input_tokens': 1, 'output_tokens': 1, 'thinking_tokens': 0}}):
            r = self.c.post('/parse/홍길동')
            body = r.get_data(as_text=True)  # patch 블록 안에서 소비해야 한다 (스트리밍 응답은 지연 평가됨)
        self.assertIn('"stage": "llm_start"', body)
        self.assertIn('event: done', body)
        self.assertTrue((self.root / 'users' / '홍길동' / 'writing-style.md').exists())

    def test_live_progress_emitted_during_generation(self):
        """LLM 호출이 수십 초 걸려도 llm_start 와 llm_done 사이가 비어 있으면 안 된다 (ROADMAP: 진행 상황 스트리밍)."""
        (self.root / 'users' / '홍길동' / 'raw' / 'apply' / 'a.md').write_text('마' * 150, encoding='utf-8')

        def fake_stream_complete(system, messages, max_tokens=4096, on_delta=None, **kw):
            for _ in range(3):
                on_delta('x' * 200)  # 3 * 200 = 600자 → 400자 기준 진행 이벤트가 최소 1개는 나와야 한다
            return {'text': '===REPORT===\n완료\n===END===\n', 'stop_reason': 'end_turn',
                    'usage': {'input_tokens': 1, 'output_tokens': 1, 'thinking_tokens': 0}}

        with patch.object(parse_route, 'stream_complete', side_effect=fake_stream_complete):
            r = self.c.post('/parse/홍길동')
            body = r.get_data(as_text=True)
        generating_count = body.count('"stage": "generating"')
        self.assertGreaterEqual(generating_count, 1)
        # llm_start 와 llm_done 사이에 generating 이 와야 진짜 "중간 진행" 이다
        self.assertLess(body.index('llm_start'), body.index('"stage": "generating"'))
        self.assertLess(body.index('"stage": "generating"'), body.index('llm_done'))

    def test_fallback_file_is_picked_up_on_next_parse(self):
        """S2-2 통합: 추출 실패 → /raw-fallback 으로 붙여넣기 저장 → 재파싱 시 원본 대신 새 파일이 읽힌다."""
        apply_dir = self.root / 'users' / '홍길동' / 'raw' / 'apply'
        (apply_dir / 'ok.md').write_text('정상적으로 읽히는 파일입니다. ' * 10, encoding='utf-8')
        (apply_dir / '깨진파일.hwp').write_bytes(b'\x00\x01broken')  # hwp5txt 가 못 읽는 가짜 파일

        fake = {'text': '===REPORT===\n완료\n===END===\n', 'stop_reason': 'end_turn',
                'usage': {'input_tokens': 1, 'output_tokens': 1, 'thinking_tokens': 0}}
        with patch.object(parse_route, 'stream_complete', return_value=fake):
            r1 = self.c.post('/parse/홍길동')
            body1 = r1.get_data(as_text=True)
        self.assertIn('"file": "\uae68\uc9c4\ud30c\uc77c.hwp", "kind": "apply", "ok": false', body1)

        # 사용자가 실패한 파일 내용을 직접 붙여넣는다
        r2 = self.c.post('/raw-fallback/홍길동', json={
            'kind': 'apply', 'filename': '깨진파일.hwp',
            'content': '사용자가 직접 붙여넣은 진짜 내용입니다. ' * 5,
        })
        self.assertTrue(r2.get_json()['success'])
        self.assertTrue((apply_dir / '깨진파일.붙여넣기.md').exists())

        # 재파싱하면 원본은 여전히 실패하지만, 붙여넣은 사본은 성공해야 한다
        with patch.object(parse_route, 'stream_complete', return_value=fake):
            r3 = self.c.post('/parse/홍길동?force=1')
            body3 = r3.get_data(as_text=True)
        self.assertIn('"file": "\uae68\uc9c4\ud30c\uc77c.hwp", "kind": "apply", "ok": false', body3)
        self.assertIn('"file": "\uae68\uc9c4\ud30c\uc77c.\ubd99\uc5ec\ub123\uae30.md", "kind": "apply", "ok": true', body3)
        # 원본은 여전히 그대로다 (raw/ 절대 수정 금지)
        self.assertEqual((apply_dir / '깨진파일.hwp').read_bytes(), b'\x00\x01broken')

    def test_force_bypasses_existing_check(self):
        (self.root / 'users' / '홍길동' / 'experiences' / 'a.md').write_text('x', encoding='utf-8')
        (self.root / 'users' / '홍길동' / 'raw' / 'apply' / 'a.md').write_text('라' * 150, encoding='utf-8')
        with patch.object(parse_route, 'stream_complete',
                           return_value={'text': '===REPORT===\n완료\n===END===\n', 'stop_reason': 'end_turn',
                                         'usage': {'input_tokens': 1, 'output_tokens': 1, 'thinking_tokens': 0}}):
            r = self.c.post('/parse/홍길동?force=1')
            body = r.get_data(as_text=True)
        self.assertIn('event: done', body)


if __name__ == '__main__':
    unittest.main()
