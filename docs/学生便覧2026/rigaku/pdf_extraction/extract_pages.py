# -*- coding: utf-8 -*-
import pdfplumber
import sys

PDF = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\rigaku\binran_2026_rigaku.pdf"

def main():
    pages_arg = sys.argv[1]  # comma separated 1-indexed page numbers
    out_path = sys.argv[2]
    pages = [int(x) for x in pages_arg.split(",")]
    with pdfplumber.open(PDF) as pdf:
        with open(out_path, "w", encoding="utf-8") as f:
            for p in pages:
                f.write(f"\n===== PAGE {p} =====\n")
                text = pdf.pages[p-1].extract_text() or "(empty)"
                f.write(text)
                f.write("\n")

if __name__ == "__main__":
    main()
