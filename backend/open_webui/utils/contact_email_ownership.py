"""Evidence-based contact ownership; public mailbox domains are not a rejection rule."""
import re
from urllib.parse import urlparse

CONTACT_OWNERSHIP_PROMPT = """
信箱歸屬規則：
1. 網頁出現 Email 不等於屬於目標公司。區分公司聯絡信箱、第三方信箱及證據不足。
2. 協會會員頁、展商名錄與平台頁面必須區分會員公司的資料區塊和主辦方的頁首頁尾。協會、主辦單位、網站製作商、隱私或技術客服信箱不可列為會員公司的聯絡人。
3. 只把本輪原始來源能證實歸屬的公司信箱放入 contacts；contactEmail 只能選其中一個公司信箱。來源不明就 contacts=[]、contactEmail=null，保留公司與其他有效證據，在 uncertaintyNotes 說明缺少什麼；不可因缺信箱而把合適公司判定為不符合。
4. 公司可以使用 Hinet、Gmail、Yahoo 等公共信箱；只要公司官網或公司專屬聯絡欄位有證據即可。不能因不同網域而直接排除，也不能因分數高而代替歸屬證據。
5. sourceUrl 使用實際來源 URL，sourceExcerpt 忠實摘錄含 Email 及公司歸屬的原文，不能自己拼接公司名稱或補寫不存在的證據。人工已排除的信箱不能重新推薦；禁止名單、退訂及人工寄送確認仍須遵守。
6. 例如伍晟官網上的 wuusheng@ms16.hinet.net 可作公司聯絡信箱；會員頁底部「聯絡我們 台灣包裝協會 E-mail: epack@pack.org.tw」屬協會，不是伍晟信箱。這是歸屬案例，不是封鎖整個網域；若目標本身就是協會，仍依協會自己的官方聯絡頁判斷。
7. 嚴格維持要求的 JSON schema 和欄位列舉值，不新增第三方狀態到 verificationStatus。第三方排除理由寫入既有 uncertaintyNotes，不虛構已寄送或已人工確認。
"""

def _host(value):
    try:
        parsed = urlparse(value or "")
        return (parsed.hostname or "").lower().removeprefix("www.") if parsed.scheme in ("http", "https") else ""
    except ValueError:
        return ""

def _same(domain, expected):
    return bool(domain and expected and (domain == expected or domain.endswith("." + expected)))

def _identity(value):
    return re.sub(r"[\s　,，.。()（）_/-]", "", value.lower())

def assess_contact_ownership(*, email, source_url, source_excerpt, official_website, company_name):
    email = email.strip().lower()
    domain = email.partition("@")[2]
    source_host, official_host = _host(source_url), _host(official_website)
    excerpt = source_excerpt or ""
    index = excerpt.lower().find(email)
    if not domain or index < 0:
        return "unknown"
    before = excerpt[max(0, index - 180):index]
    official = _same(source_host, official_host)
    if re.search(r"(?:網站|網頁)(?:設計|製作|建置|維護)|(?:web(?:site)?\s*(?:design|developer)|design(?:ed)?\s+by|powered\s+by|webmaster)\s*[:：]?", before[-100:], re.I):
        return "third_party"
    association = r"協會|公會|工會|主辦單位|主辦單位聯絡|展會主辦|association|chamber of commerce|organizer"
    target = _identity(company_name or "")
    target_is_publisher = len(target) >= 3 and target in _identity(before) and re.search(association, company_name or "", re.I)
    if not official and not target_is_publisher and re.search(association, before, re.I) and _same(domain, source_host):
        return "third_party"
    if official or _same(domain, official_host):
        return "company"
    if re.search(r"公司(?:電子郵件|電子信箱|信箱|郵箱|電郵|Email|E-mail)|(?:company|member)\s*(?:e-?mail)", before[-100:], re.I):
        return "company"
    if len(target) >= 3 and target in _identity(excerpt[max(0,index-180):index+len(email)+180]):
        return "company"
    return "unknown"
