import ast
import importlib.util
import re
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1] / "open_webui"
spec = importlib.util.spec_from_file_location("ownership", ROOT / "utils/contact_email_ownership.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
source = (ROOT / "routers/workflows.py").read_text(encoding="utf-8")
tree = ast.parse(source)
names = {"_public_contacts_from_text", "_normalized_company_identity", "_contact_name_from_excerpt", "_contact_title_from_excerpt"}
nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
         or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith("PROSPECT_") for t in node.targets)]
scope = {"re": re, "Any": Any, "urlparse": urlparse, "assess_contact_ownership": policy.assess_contact_ownership}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "<actual-workflow-contact-functions>", "exec"), scope)
extract = scope["_public_contacts_from_text"]

class ContactOwnershipTests(unittest.TestCase):
    def test_official_public_mailboxes_remain_usable(self):
        for domain in ("gmail.com", "ms16.hinet.net", "yahoo.com.tw", "outlook.com", "wuusheng.com.tw"):
            email = "sales@" + domain
            with self.subTest(domain=domain):
                contacts = extract("伍晟機械股份有限公司 Email: " + email,
                                   source_url="https://www.wuusheng.com.tw", official_domain="wuusheng.com.tw",
                                   company_name="伍晟機械股份有限公司")
                self.assertEqual([c["email"] for c in contacts], [email])
    def test_association_member_page_retains_company_contact_not_footer(self):
        contacts = extract("伍晟機械股份有限公司 公司電子郵件: wuusheng@ms16.hinet.net\n" + "設備製造資訊 " * 80
                           + "\n聯絡我們 台灣包裝協會 E-mail: epack@pack.org.tw",
                           source_url="https://www.pack.org.tw/member/123", official_domain="wuusheng.com.tw",
                           company_name="伍晟機械股份有限公司")
        self.assertEqual([c["email"] for c in contacts], ["wuusheng@ms16.hinet.net"])
    def test_target_association_itself_is_allowed(self):
        contacts = extract("台灣包裝協會 E-mail: epack@pack.org.tw", source_url="https://www.pack.org.tw",
                           official_domain="pack.org.tw", company_name="台灣包裝協會")
        self.assertEqual([c["email"] for c in contacts], ["epack@pack.org.tw"])
    def test_web_designer_not_selected(self):
        contacts = extract("伍晟機械股份有限公司 公司電子郵件: sales@gmail.com\n" + "設備製造資訊 " * 80
                           + "\n網站設計: studio@design.example", source_url="https://www.wuusheng.com.tw",
                           official_domain="wuusheng.com.tw", company_name="伍晟機械股份有限公司")
        self.assertEqual([c["email"] for c in contacts], ["sales@gmail.com"])
    def test_existing_joined_mail_and_phone_cleanup_survives(self):
        contacts = extract("公司 Email: 253-7206sales@factory.example\ninfo@factory.comservice@factory.com",
                           source_url="https://factory.example", official_domain="factory.example", company_name="公司")
        self.assertIn("sales@factory.example", [c["email"] for c in contacts])
        self.assertIn("info@factory.com", [c["email"] for c in contacts])
        self.assertIn("service@factory.com", [c["email"] for c in contacts])
    def test_unknown_directory_contact_is_not_claimed_as_company(self):
        status = policy.assess_contact_ownership(email="other@gmail.com", source_url="https://directory.example",
                source_excerpt="聯絡我們 other@gmail.com", official_website="https://factory.example", company_name="甲乙公司")
        self.assertEqual(status, "unknown")
    def test_actual_workflow_template_contains_guardrails_and_schema_compatibility(self):
        self.assertIn('prompt += "\\n\\n" + CONTACT_OWNERSHIP_PROMPT', source)
        self.assertIn("Hinet", policy.CONTACT_OWNERSHIP_PROMPT)
        self.assertIn("verificationStatus", policy.CONTACT_OWNERSHIP_PROMPT)
        self.assertIn("保留公司", policy.CONTACT_OWNERSHIP_PROMPT)
        compile(source, "workflows.py", "exec")

if __name__ == "__main__":
    unittest.main()
