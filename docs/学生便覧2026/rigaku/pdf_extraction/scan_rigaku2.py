# -*- coding: utf-8 -*-
import pdfplumber

PDF = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\rigaku\binran_2026_rigaku.pdf"
OUT = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\rigaku\rigaku_scan_result2.txt"

keywords = [
    "偶数",
    "奇数",
    "学籍番号",
    "コース",
    "分岐",
    "指導教員",
    "研究室配属",
    "配属",
    "卒業研究",
    "特別研究",
    "数学講究",
]

with pdfplumber.open(PDF) as pdf:
    total = len(pdf.pages)
    results = {kw: [] for kw in keywords}
    for i, page in enumerate(pdf.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        for kw in keywords:
            if kw in text:
                results[kw].append(i + 1)

with open(OUT, "w", encoding="utf-8") as f:
    f.write(f"Total pages: {total}\n\n")
    for kw in keywords:
        f.write(f"[{kw}] -> pages: {results[kw]}\n")
print("done")
