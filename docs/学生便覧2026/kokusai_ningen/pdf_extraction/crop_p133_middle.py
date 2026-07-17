import fitz
path = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\kokusai_ningen\handbook2026_kokusai_ningen.pdf"
doc = fitz.open(path)
page = doc[132]  # page133 (1-indexed) -> 0-indexed 132
rect = page.rect
print("page rect", rect)
# crop bottom-middle area (学科専門科目 rows), roughly y 55%-85% of page height, full width
clip = fitz.Rect(rect.x0, rect.y0 + rect.height*0.42, rect.x1, rect.y0 + rect.height*0.75)
pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=clip)
out = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\kokusai_ningen\kokusai_p133_gakka_senmon_hires.png"
pix.save(out)
print("saved", out)
