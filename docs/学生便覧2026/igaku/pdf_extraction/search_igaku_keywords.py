# -*- coding: utf-8 -*-
import re

path = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\igaku\igaku_full.txt"
with open(path, encoding="utf-8") as f:
    content = f.read()

# split by page markers
pages = re.split(r"===== PAGE (\d+) =====\n", content)
# pages[0] is empty preamble, then alternating pagenum, text
page_texts = {}
for i in range(1, len(pages), 2):
    num = int(pages[i])
    text = pages[i+1]
    page_texts[num] = text

keywords = [
    "医学科", "医療創成工学科", "保健学科", "作業療法学専攻", "検査技術科学専攻",
    "理学療法学専攻", "看護学専攻",
    "履修科目の登録の上限", "登録の上限", "CAP",
    "別表第1", "別表第2", "別表第3", "別表1", "別表2",
    "◎", "○", "卒業研究", "卒業論文", "臨床実習",
    "履修科目の登録の上限に関する細則",
]

out_path = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\igaku\igaku_keyword_pages.txt"
with open(out_path, "w", encoding="utf-8") as out:
    for kw in keywords:
        matches = [num for num, text in sorted(page_texts.items()) if kw in text]
        out.write(f"[{kw}] -> pages: {matches}\n")

print("done")
