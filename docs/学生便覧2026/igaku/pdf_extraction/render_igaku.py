import fitz
import sys

pdf_path = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\igaku\binran_2026_igaku.pdf"
doc = fitz.open(pdf_path)

pages = [int(x) for x in sys.argv[1].split(",")]
for p in pages:
    page = doc[p - 1]  # 0-indexed
    pix = page.get_pixmap(matrix=fitz.Matrix(2.2, 2.2))
    out = rf"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\igaku\igaku_p{p}.png"
    pix.save(out)
print("done")
