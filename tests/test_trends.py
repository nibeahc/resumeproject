"""자소서 트렌드 리서치 테스트 (고도화). Anthropic web_search 도구 호출은 스텁으로 대체하고,
파일 저장/만료 판정/텍스트-인용 추출은 실제 동작을 검증한다."""
import shutil
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import trends  # noqa: E402


@dataclass
class FakeCitation:
    url: str
    title: str = ''


@dataclass
class FakeBlock:
    type: str
    text: str = ''
    citations: Optional[List[FakeCitation]] = field(default_factory=list)


def fake_response(blocks):
    r = MagicMock()
    r.content = blocks
    return r


class ExtractTextAndCitationsTest(unittest.TestCase):
    def test_text_blocks_joined_in_order(self):
        blocks = [FakeBlock(type='text', text='첫 문단'), FakeBlock(type='text', text='둘째 문단')]
        body, sources = trends._extract_text_and_citations(blocks)
        self.assertEqual(body, '첫 문단\n둘째 문단')
        self.assertEqual(sources, [])

    def test_non_text_blocks_ignored(self):
        blocks = [
            FakeBlock(type='server_tool_use'),
            FakeBlock(type='web_search_tool_result'),
            FakeBlock(type='text', text='본문'),
        ]
        body, _ = trends._extract_text_and_citations(blocks)
        self.assertEqual(body, '본문')

    def test_citations_deduplicated_by_url(self):
        blocks = [
            FakeBlock(type='text', text='a', citations=[FakeCitation(url='https://x.com', title='X')]),
            FakeBlock(type='text', text='b', citations=[FakeCitation(url='https://x.com', title='X 다시')]),
            FakeBlock(type='text', text='c', citations=[FakeCitation(url='https://y.com', title='Y')]),
        ]
        _, sources = trends._extract_text_and_citations(blocks)
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]['url'], 'https://x.com')

    def test_no_text_blocks_returns_empty(self):
        body, sources = trends._extract_text_and_citations([FakeBlock(type='server_tool_use')])
        self.assertEqual(body, '')
        self.assertEqual(sources, [])

    def test_narration_before_search_results_excluded_from_final_body(self):
        """실측(2026-09-27)으로 발견: 검색 사이사이 "~를 더 찾아보겠습니다" 같은 중간 서술이
        최종 문서에 섞여 들어갔다. 마지막 검색 결과 이후에 나온 text 만 최종 답변으로 쳐야 한다."""
        blocks = [
            FakeBlock(type='text', text='자소서 트렌드를 검색하겠습니다.'),
            FakeBlock(type='server_tool_use'),
            FakeBlock(type='web_search_tool_result'),
            FakeBlock(type='text', text='추가로 더 찾아보겠습니다.'),
            FakeBlock(type='server_tool_use'),
            FakeBlock(type='web_search_tool_result'),
            FakeBlock(type='text', text='# 최종 정리\n\n실제 답변 내용'),
        ]
        body, _ = trends._extract_text_and_citations(blocks)
        self.assertEqual(body, '# 최종 정리\n\n실제 답변 내용')
        self.assertNotIn('검색하겠습니다', body)
        self.assertNotIn('더 찾아보겠습니다', body)

    def test_citations_from_narration_blocks_still_collected(self):
        """중간 서술 블록에 인용이 달려 있어도(드물지만) 출처 목록에서는 안 놓친다 —
        본문에서만 빼는 것이지, 인용 자체를 버리는 게 아니다."""
        blocks = [
            FakeBlock(type='text', text='중간 서술', citations=[FakeCitation(url='https://mid.com', title='중간출처')]),
            FakeBlock(type='web_search_tool_result'),
            FakeBlock(type='text', text='최종 답변', citations=[FakeCitation(url='https://final.com', title='최종출처')]),
        ]
        body, sources = trends._extract_text_and_citations(blocks)
        self.assertEqual(body, '최종 답변')
        urls = {s['url'] for s in sources}
        self.assertEqual(urls, {'https://mid.com', 'https://final.com'})


class StripLeadingNarrationTest(unittest.TestCase):
    """실측(2026-09-27)으로 확인된 잔여 문제: 같은 텍스트 블록 안에서도 "충분한 정보를
    확보했습니다. 이제 정리하겠습니다." 같은 전환 문장이 실제 내용 앞에 붙어 나왔다."""

    def test_strips_prose_before_first_heading(self):
        body = '충분한 정보를 확보했습니다. 이제 정리하겠습니다.\n\n## 1. 구조\n두괄식 선호'
        self.assertEqual(trends._strip_leading_narration(body), '## 1. 구조\n두괄식 선호')

    def test_already_starting_with_heading_untouched(self):
        body = '## 1. 구조\n두괄식 선호'
        self.assertEqual(trends._strip_leading_narration(body), body)

    def test_no_heading_at_all_left_untouched(self):
        body = '헤딩 없이 평문으로만 온 드문 경우'
        self.assertEqual(trends._strip_leading_narration(body), body)


class FreshnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / 'trends.md'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_needs_refresh(self):
        self.assertTrue(trends.needs_refresh(self.path))
        self.assertIsNone(trends.last_updated(self.path))

    def test_recent_file_does_not_need_refresh(self):
        today = date.today().strftime('%Y-%m-%d')
        self.path.write_text(f'> 최종 갱신일: {today}\n\n내용', encoding='utf-8')
        self.assertFalse(trends.needs_refresh(self.path))
        self.assertEqual(trends.last_updated(self.path), date.today())

    def test_old_file_needs_refresh(self):
        old = (date.today() - timedelta(days=40)).strftime('%Y-%m-%d')
        self.path.write_text(f'> 최종 갱신일: {old}\n\n내용', encoding='utf-8')
        self.assertTrue(trends.needs_refresh(self.path, max_age_days=30))

    def test_exactly_at_boundary_does_not_need_refresh(self):
        boundary = (date.today() - timedelta(days=30)).strftime('%Y-%m-%d')
        self.path.write_text(f'> 최종 갱신일: {boundary}\n\n내용', encoding='utf-8')
        self.assertFalse(trends.needs_refresh(self.path, max_age_days=30))

    def test_malformed_date_treated_as_missing(self):
        self.path.write_text('내용만 있고 갱신일 줄이 없음', encoding='utf-8')
        self.assertTrue(trends.needs_refresh(self.path))


class ResearchTrendsTest(unittest.TestCase):
    def test_successful_research_builds_markdown_with_sources(self):
        blocks = [FakeBlock(type='text', text='## 구조\n두괄식 선호',
                            citations=[FakeCitation(url='https://a.com', title='기사A')])]
        with patch.object(trends, '_get_client') as get_client:
            get_client.return_value.messages.create.return_value = fake_response(blocks)
            result = trends.research_trends()
        self.assertTrue(result.ok)
        self.assertIn('두괄식 선호', result.markdown)
        self.assertIn('기사A', result.markdown)
        self.assertIn('최종 갱신일', result.markdown)
        self.assertEqual(result.sources, [{'url': 'https://a.com', 'title': '기사A'}])

    def test_web_search_tool_included_in_request(self):
        blocks = [FakeBlock(type='text', text='내용')]
        with patch.object(trends, '_get_client') as get_client:
            get_client.return_value.messages.create.return_value = fake_response(blocks)
            trends.research_trends(max_uses=3)
            kwargs = get_client.return_value.messages.create.call_args.kwargs
        self.assertEqual(kwargs['tools'], [{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 3}])
        self.assertEqual(kwargs['thinking'], {'type': 'disabled'})  # S1-1 교훈 재사용

    def test_api_error_reported_not_raised(self):
        with patch.object(trends, '_get_client') as get_client:
            get_client.return_value.messages.create.side_effect = RuntimeError('네트워크 오류')
            result = trends.research_trends()
        self.assertFalse(result.ok)
        self.assertIn('네트워크 오류', result.error)

    def test_empty_text_response_reported_as_error(self):
        with patch.object(trends, '_get_client') as get_client:
            get_client.return_value.messages.create.return_value = fake_response([])
            result = trends.research_trends()
        self.assertFalse(result.ok)

    def test_truncated_response_reported_as_error_not_saved(self):
        """실측(2026-09-27)으로 발견한 버그의 회귀 테스트: max_tokens=2048 이던 시절 실제
        리서치가 문장 중간에 잘렸는데(예: "쉼표나 별표 같은 기") 그대로 저장돼 화면에 노출됐다.
        stop_reason 이 'max_tokens' 면 텍스트가 있어도 실패로 취급해야 한다."""
        blocks = [FakeBlock(type='text', text='## 3. AI 문체\n쉼표나 별표 같은 기')]
        with patch.object(trends, '_get_client') as get_client:
            response = fake_response(blocks)
            response.stop_reason = 'max_tokens'
            get_client.return_value.messages.create.return_value = response
            result = trends.research_trends()
        self.assertFalse(result.ok)
        self.assertIn('max_tokens', result.error)

    def test_requests_enough_max_tokens_for_full_report(self):
        """2048 은 실측에서 5개 항목 + 인용을 다 쓰기 전에 잘렸다 — 넉넉한 값으로 올렸는지 확인."""
        blocks = [FakeBlock(type='text', text='내용')]
        with patch.object(trends, '_get_client') as get_client:
            get_client.return_value.messages.create.return_value = fake_response(blocks)
            trends.research_trends()
            kwargs = get_client.return_value.messages.create.call_args.kwargs
        self.assertGreaterEqual(kwargs['max_tokens'], 8192)


class EnsureFreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / 'trends.md'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_skips_api_call_when_already_fresh(self):
        today = date.today().strftime('%Y-%m-%d')
        self.path.write_text(f'> 최종 갱신일: {today}\n\n내용', encoding='utf-8')
        with patch.object(trends, 'research_trends') as rt:
            result = trends.ensure_fresh(self.path)
        rt.assert_not_called()
        self.assertIsNone(result)

    def test_calls_api_and_saves_when_stale(self):
        old = (date.today() - timedelta(days=100)).strftime('%Y-%m-%d')
        self.path.write_text(f'> 최종 갱신일: {old}\n\n낡은 내용', encoding='utf-8')
        with patch.object(trends, 'research_trends',
                          return_value=trends.TrendsResult(ok=True, markdown='새 문서 내용')) as rt:
            result = trends.ensure_fresh(self.path)
        rt.assert_called_once()
        self.assertTrue(result.ok)
        self.assertEqual(self.path.read_text(encoding='utf-8'), '새 문서 내용')

    def test_failed_research_does_not_overwrite_existing_file(self):
        old = (date.today() - timedelta(days=100)).strftime('%Y-%m-%d')
        self.path.write_text(f'> 최종 갱신일: {old}\n\n낡은 내용', encoding='utf-8')
        with patch.object(trends, 'research_trends',
                          return_value=trends.TrendsResult(ok=False, error='실패')):
            trends.ensure_fresh(self.path)
        self.assertIn('낡은 내용', self.path.read_text(encoding='utf-8'))


class TriggerBackgroundRefreshTest(unittest.TestCase):
    """"앱 자체가 자동으로 주기적으로 검색"(사용자 요청, 2026-09-27)의 핵심 동작."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / 'trends.md'
        trends._refreshing = False  # 다른 테스트가 남긴 상태를 초기화

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        trends._refreshing = False

    def test_returns_false_and_skips_api_when_already_fresh(self):
        today = date.today().strftime('%Y-%m-%d')
        self.path.write_text(f'> 최종 갱신일: {today}\n\n내용', encoding='utf-8')
        with patch.object(trends, 'ensure_fresh') as ef:
            started = trends.trigger_background_refresh(self.path)
        self.assertFalse(started)
        ef.assert_not_called()

    def test_starts_background_thread_and_does_not_duplicate(self):
        release = threading.Event()

        def slow_ensure_fresh(*a, **kw):
            release.wait(timeout=2)
            return None

        with patch.object(trends, 'ensure_fresh', side_effect=slow_ensure_fresh) as ef:
            started = trends.trigger_background_refresh(self.path)
            self.assertTrue(started)
            # 진행 중일 때 페이지를 또 열어도(=또 호출해도) API 를 중복 호출하지 않는다
            started_again = trends.trigger_background_refresh(self.path)
            self.assertFalse(started_again)
            release.set()

            for _ in range(40):
                if not trends._refreshing:
                    break
                time.sleep(0.05)
            self.assertFalse(trends._refreshing)
            ef.assert_called_once()

    def test_does_not_crash_request_when_background_research_fails(self):
        with patch.object(trends, 'ensure_fresh', side_effect=RuntimeError('네트워크 오류')):
            started = trends.trigger_background_refresh(self.path)
            self.assertTrue(started)
            for _ in range(40):
                if not trends._refreshing:
                    break
                time.sleep(0.05)
        self.assertFalse(trends._refreshing)  # 실패해도 플래그는 풀려서 다음 기회에 재시도 가능


if __name__ == '__main__':
    unittest.main()
