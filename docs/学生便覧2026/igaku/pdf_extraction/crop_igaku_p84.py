import fitz
pdf_path = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\igaku\binran_2026_igaku.pdf"
doc = fitz.open(pdf_path)
page = doc[83]  # PDF page 84, 0-indexed
pix = page.get_pixmap(matrix=fitz.Matrix(4, 4))
# crop to top area with table header + first ~15 rows
rect = pix.irect
w, h = rect.width, rect.height
# crop top ~40% of page (header + first rows)
clip = fitz.Rect(0, 0, page.rect.width, page.rect.height * 0.42)
pix2 = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=clip)
pix2.save(r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\igaku\igaku_p84_crop_top.png")
print("done")
