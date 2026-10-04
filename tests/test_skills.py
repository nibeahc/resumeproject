"""스킬 로더와 기계 판독 계약 (S11-15, 설계검토 A-3).

핵심은 두 가지다.
1. 계약(frontmatter)이 프롬프트 본문에 섞이지 않는다 — 섞이면 YAML 이 모델에게 간다.
2. 오타난 정책이 조용히 무시되지 않는다 — 무시되면 코드는 기본값으로 도는데
   스킬 파일에 적힌 정책은 지켜지는 줄 알게 된다.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import skills  # noqa: E402
from lib.coverletter import SUPPORTED_CONTRACT_VERSION, validate_contract  # noqa: E402
from lib.skills import ContractError  # noqa: E402


class FrontmatterParseTest(unittest.TestCase):
    DOC = """---
contract_version: 1
workflow: coverletter

# 주석은 무시한다
experience_policy:
  max_count: 2
  second_experience_requires_shared_claim: true
  label: "따옴표 안의 값"
---

# 본문 제목

내용입니다.
"""

    def test_parses_nested_and_scalar_types(self):
        data, _ = skills.parse_frontmatter(self.DOC)
        self.assertEqual(data['contract_version'], 1)
        self.assertEqual(data['workflow'], 'coverletter')
        self.assertEqual(data['experience_policy']['max_count'], 2)
        self.assertIs(data['experience_policy']['second_experience_requires_shared_claim'], True)
        self.assertEqual(data['experience_policy']['label'], '따옴표 안의 값')

    def test_body_has_no_yaml(self):
        _, body = skills.parse_frontmatter(self.DOC)
        self.assertNotIn('contract_version', body)
        self.assertTrue(body.lstrip().startswith('# 본문 제목'))

    def test_no_frontmatter_returns_whole_text(self):
        data, body = skills.parse_frontmatter('# 그냥 본문\n\n내용')
        self.assertEqual(data, {})
        self.assertEqual(body, '# 그냥 본문\n\n내용')

    def test_indented_key_without_parent_is_error(self):
        with self.assertRaises(ContractError):
            skills.parse_frontmatter('---\n  max_count: 2\n---\n본문')

    def test_unreadable_line_is_error(self):
        with self.assertRaises(ContractError):
            skills.parse_frontmatter('---\ncontract_version 1\n---\n본문')


class LoadSkillTest(unittest.TestCase):
    def test_load_skill_strips_contract(self):
        """계약이 프롬프트에 실리면 모델이 YAML 을 읽게 된다."""
        body = skills.load_skill('write-coverletter')
        self.assertNotIn('contract_version', body)
        self.assertIn('Skill: write-coverletter', body)

    def test_load_frontmatter_reads_the_contract(self):
        data = skills.load_frontmatter('write-coverletter')
        self.assertEqual(data['contract_version'], SUPPORTED_CONTRACT_VERSION)
        self.assertEqual(data['experience_policy']['max_count'], 2)

    def test_skill_without_contract_still_loads(self):
        body = skills.load_skill('review-coverletter')
        self.assertTrue(body.strip())
        self.assertEqual(skills.load_frontmatter('review-coverletter'), {})

    def test_unknown_skill_raises(self):
        with self.assertRaises(skills.SkillNotFoundError):
            skills.load_skill('없는스킬')

    def test_path_traversal_rejected(self):
        for bad in ('../secret', 'a/b', '..'):
            with self.assertRaises(ValueError):
                skills.load_skill(bad)


class ContractValidationTest(unittest.TestCase):
    """설계검토 A-3: "지원하지 않는 계약 필드는 조용히 무시하지 않고 오류로 처리한다"."""

    def base(self, **over):
        data = {'contract_version': SUPPORTED_CONTRACT_VERSION,
                'experience_policy': {'max_count': 2}}
        data.update(over)
        return data

    def test_valid_contract_passes(self):
        self.assertEqual(validate_contract(self.base())['experience_policy']['max_count'], 2)

    def test_unknown_top_level_key_rejected(self):
        with self.assertRaises(ContractError):
            validate_contract(self.base(experiance_policy={'max_count': 2}))

    def test_typo_in_nested_key_rejected(self):
        with self.assertRaises(ContractError):
            validate_contract({'contract_version': 1, 'experience_policy': {'max_counts': 2}})

    def test_wrong_type_rejected(self):
        with self.assertRaises(ContractError):
            validate_contract({'contract_version': 1, 'experience_policy': {'max_count': 'two'}})

    def test_bool_is_not_accepted_where_int_expected(self):
        """파이썬에서 True 는 int 의 하위형이다. 그냥 isinstance 로 보면 통과한다."""
        with self.assertRaises(ContractError):
            validate_contract({'contract_version': 1, 'experience_policy': {'max_count': True}})

    def test_unsupported_version_rejected(self):
        with self.assertRaises(ContractError):
            validate_contract({'contract_version': 99})

    def test_missing_version_rejected(self):
        with self.assertRaises(ContractError):
            validate_contract({'workflow': 'coverletter'})


class PolicyFlowsIntoCodeTest(unittest.TestCase):
    """계약값이 실제로 코드에 쓰이는지. 안 쓰이면 계약은 장식이다."""

    def test_constants_derive_from_contract(self):
        from lib import coverletter as C
        p = C.POLICY
        self.assertEqual(C.MAX_RETRIES, p['retry_policy']['max_length_retries'])
        self.assertEqual(C.MAX_REPAIRS, p['retry_policy']['max_repair_rounds'])
        self.assertEqual(C.MAX_CHAR_LIMIT, p['length_policy']['max_chars'])
        self.assertEqual(C.LENGTH_TOLERANCE, p['length_policy']['tolerance_pct'] / 100)
        self.assertEqual(C.COMPANY_EVIDENCE_MAX_AGE_DAYS,
                         p['company_evidence_policy']['max_age_days'])
        self.assertEqual(C.MAX_EXPERIENCES_BY_CONTRACT, p['experience_policy']['max_count'])
        self.assertIs(C.COMPANY_EVIDENCE_REQUIRED,
                      p['preconditions']['company_evidence'] == 'required')


if __name__ == '__main__':
    unittest.main()
