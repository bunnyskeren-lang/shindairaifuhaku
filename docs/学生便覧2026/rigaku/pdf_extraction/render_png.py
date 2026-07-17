# -*- coding: utf-8 -*-
import fitz
import sys

PDF = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\rigaku\binran_2026_rigaku.pdf"

def main():
    pages = [int(x) for x in sys.argv[1].split(",")]
    doc = fitz.open(PDF)
    for p in pages:
        page = doc[p-1]
        pix = page.get_pixmap(matrix=fitz.Matrix(2.2, 2.2))
        out = rf"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\rigaku\rigaku_p{p}.png"
        pix.save(out)
        print(out)

if __name__ == "__main__":
    main()
