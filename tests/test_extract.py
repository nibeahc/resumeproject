"""lib/extract.py 테스트 (S2-1). 성공/실패가 항상 명시적으로 나오는지가 핵심이다."""
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import extract as E  # noqa: E402


class TmpDirTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class MdTxtTest(TmpDirTestCase):
    def test_reads_long_enough_text(self):
        p = self.tmp / 'a.md'
        p.write_text('가' * 150, encoding='utf-8')
        r = E.extract(p)
        self.assertTrue(r.ok)
        self.assertEqual(len(r.text), 150)
        self.assertEqual(r.method, 'text')
        self.assertIsNone(r.error)

    def test_short_text_fails_explicitly(self):
        p = self.tmp / 'a.txt'
        p.write_text('짧음', encoding='utf-8')
        r = E.extract(p)
        self.assertFalse(r.ok)
        self.assertIn('100자 미만', r.error)
        self.assertEqual(r.text, '')


class DocxHwpxTest(TmpDirTestCase):
    def _make_zip(self, name, member, xml):
        p = self.tmp / name
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr(member, xml)
        return p

    def test_docx_extracts_from_document_xml(self):
        xml = '<root>' + ('<w:t>본문 문장입니다</w:t>' * 15) + '</root>'
        p = self._make_zip('a.docx', 'word/document.xml', xml)
        r = E.extract(p)
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.method, 'docx-xml')
        self.assertIn('본문', r.text)

    def test_hwpx_extracts_from_section0_xml(self):
        xml = '<root>' + ('<hp:t>본문 문장입니다</hp:t>' * 15) + '</root>'
        p = self._make_zip('a.hwpx', 'Contents/section0.xml', xml)
        r = E.extract(p)
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.method, 'hwpx-xml')

    def test_not_a_zip_fails_explicitly_not_crash(self):
        p = self.tmp / 'broken.hwpx'
        p.write_bytes(b'this is not a zip file')
        r = E.extract(p)
        self.assertFalse(r.ok)
        self.assertIn('구조를 읽을 수 없음', r.error)

    def test_zip_without_expected_member_fails_explicitly(self):
        p = self.tmp / 'a.docx'
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('other.xml', '<x/>')
        r = E.extract(p)
        self.assertFalse(r.ok)
        self.assertIn('구조를 읽을 수 없음', r.error)


class FindHwp5txtTest(TmpDirTestCase):
    """.env 의 HWP5TXT_PATH 가 PATH 와 하드코딩 폴백보다 우선해야 한다."""

    def test_configured_path_takes_priority(self):
        configured = self.tmp / 'configured_hwp5txt'
        configured.write_text('x', encoding='utf-8')
        with patch('lib.config.hwp5txt_path', return_value=configured), \
             patch.object(E, 'HWP5TXT_FALLBACK', self.tmp / 'never_used'):
            self.assertEqual(E.find_hwp5txt(), configured)

    def test_falls_back_when_configured_path_does_not_exist(self):
        with patch('lib.config.hwp5txt_path', return_value=self.tmp / 'no_such_file'), \
             patch('shutil.which', return_value=None):
            fallback = self.tmp / 'dev_fallback'
            fallback.write_text('x', encoding='utf-8')
            with patch.object(E, 'HWP5TXT_FALLBACK', fallback):
                self.assertEqual(E.find_hwp5txt(), fallback)

    def test_path_used_when_not_configured(self):
        with patch('lib.config.hwp5txt_path', return_value=None), \
             patch('shutil.which', return_value='/usr/local/bin/hwp5txt'):
            self.assertEqual(E.find_hwp5txt(), Path('/usr/local/bin/hwp5txt'))


class HwpTest(TmpDirTestCase):
    def test_no_hwp5txt_found_fails_explicitly(self):
        p = self.tmp / 'a.hwp'
        p.write_bytes(b'x')
        with patch.object(E, 'find_hwp5txt', return_value=None):
            r = E.extract(p)
        self.assertFalse(r.ok)
        self.assertIn('hwp5txt', r.error)
        self.assertIn('pyhwp', r.error)

    def test_hwp5txt_nonzero_exit_reported_not_crashed(self):
        p = self.tmp / 'a.hwp'
        p.write_bytes(b'x')
        fake_exe = self.tmp / 'fake_hwp5txt.sh'
        fake_exe.write_text('#!/bin/sh\necho "broken file" 1>&2\nexit 1\n', encoding='utf-8')
        fake_exe.chmod(0o755)
        with patch.object(E, 'find_hwp5txt', return_value=fake_exe):
            r = E.extract(p)
        self.assertFalse(r.ok)
        self.assertIn('변환 실패', r.error)

    def test_table_heavy_hwp_under_min_chars_fails_explicitly(self):
        """실측: 표·이미지 위주 문서는 hwp5txt 가 <표>, <그림> 같은 placeholder 만 뽑아낸다.
        exit code 는 0 이라 성공한 척할 수 있는데, 실제로는 쓸모 있는 텍스트가 없다."""
        p = self.tmp / 'a.hwp'
        p.write_bytes(b'x')
        fake_exe = self.tmp / 'fake_hwp5txt.sh'
        fake_exe.write_text('#!/bin/sh\nprintf "<그림>\\n\\n<표>\\n"\nexit 0\n', encoding='utf-8')
        fake_exe.chmod(0o755)
        with patch.object(E, 'find_hwp5txt', return_value=fake_exe):
            r = E.extract(p)
        self.assertFalse(r.ok)
        self.assertIn('100자 미만', r.error)


class PdfTest(TmpDirTestCase):
    def test_missing_pypdf_dependency_reported(self):
        p = self.tmp / 'a.pdf'
        p.write_bytes(b'%PDF-1.4')
        real_import = __builtins__['__import__'] if isinstance(__builtins__, dict) else __builtins__.__import__

        def blocked_import(name, *a, **kw):
            if name == 'pypdf':
                raise ImportError('no module named pypdf')
            return real_import(name, *a, **kw)

        with patch('builtins.__import__', side_effect=blocked_import):
            r = E.extract(p)
        self.assertFalse(r.ok)
        self.assertIn('pypdf', r.error)

    def test_corrupt_pdf_fails_explicitly_not_crash(self):
        p = self.tmp / 'broken.pdf'
        p.write_bytes(b'not a real pdf')
        r = E.extract(p)
        self.assertFalse(r.ok)
        self.assertIn('pypdf', r.method)


class MiscTest(TmpDirTestCase):
    def test_unsupported_extension(self):
        p = self.tmp / 'a.xlsx'
        p.write_bytes(b'x' * 200)
        r = E.extract(p)
        self.assertFalse(r.ok)
        self.assertIn('지원하지 않음', r.error)

    def test_missing_file(self):
        r = E.extract(self.tmp / 'nope.md')
        self.assertFalse(r.ok)
        self.assertIn('존재하지 않음', r.error)

    def test_extract_all_pairs_path_with_result(self):
        p1 = self.tmp / 'a.md'; p1.write_text('가' * 150, encoding='utf-8')
        p2 = self.tmp / 'b.md'; p2.write_text('짧음', encoding='utf-8')
        results = E.extract_all([p1, p2])
        self.assertEqual([p for p, _ in results], [p1, p2])
        self.assertTrue(results[0][1].ok)
        self.assertFalse(results[1][1].ok)


if __name__ == '__main__':
    unittest.main()
