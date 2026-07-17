# -*- coding: utf-8 -*-
import pdfplumber
import re
import sys

PDF = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\rigaku\binran_2026_rigaku.pdf"
OUT = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\rigaku\rigaku_scan_result.txt"

keywords = [
    "履修科目の登録の上限",
    "CAP",
    "登録単位数の上限",
    "登録できる単位数",
    "理学部規則",
    "別表第1",
    "別表第２",
    "別表第2",
    "別表１",
    "別表２",
    "◎",
    "必修科目",
    "履修上の注意",
    "早期履修",
    "卒業研究",
    "卒業論文",
    "履修要件",
    "第５条",
    "第5条",
    "第６条",
    "第6条",
    "履修科目の登録",
    "数学科",
    "物理学科",
    "化学科",
    "生物学科",
    "惑星学科",
]

with pdfplumber.open(PDF) as pdf:
    total = len(pdf.pages)
    results = {kw: [] for kw in keywords}
    all_text = {}
    for i, page in enumerate(pdf.pages):
        try:
            text = page.extract_text() or ""
        except Exception as e:
            text = ""
        all_text[i] = text
        for kw in keywords:
            if kw in text:
                results[kw].append(i + 1)  # 1-indexed page number

with open(OUT, "w", encoding="utf-8") as f:
    f.write(f"Total pages: {total}\n\n")
    for kw in keywords:
        f.write(f"[{kw}] -> pages: {results[kw]}\n")

print("done")
