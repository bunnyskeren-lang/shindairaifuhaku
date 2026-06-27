import asyncio, os, sys, re, urllib.request
from dotenv import load_dotenv
load_dotenv('.env.dev')
sys.path.insert(0, '..')

def fetch_html(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

def parse_numbering_code(html):
    # "ナンバリングコード" の後に来る B1BB... パターンを探す
    m = re.search(r'ナンバリングコード.*?([A-Z]\d[A-Z]{2}\d{3})', html, re.DOTALL)
    return m.group(1) if m else None

# サンプル数件で確認
CODES = ["3B185", "3B170", "4B373", "3B256", "3B318"]
for code in CODES:
    url = f"https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/06/data/2026_{code}.html"
    html = fetch_html(url)
    nc = parse_numbering_code(html) if html else None
    print(f"  {code}: {nc}")
