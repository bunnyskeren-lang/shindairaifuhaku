# -*- coding: utf-8 -*-
import re, sys

path = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\igaku\igaku_full.txt"
with open(path, encoding="utf-8") as f:
    content = f.read()

pages = re.split(r"===== PAGE (\d+) =====\n", content)
page_texts = {}
for i in range(1, len(pages), 2):
    num = int(pages[i])
    text = pages[i+1]
    page_texts[num] = text

targets = [int(x) for x in sys.argv[1].split(",")]
out_path = sys.argv[2]
with open(out_path, "w", encoding="utf-8") as out:
    for t in targets:
        out.write(f"\n===== PAGE {t} =====\n")
        out.write(page_texts.get(t, "(missing)"))
        out.write("\n")
print("done")
