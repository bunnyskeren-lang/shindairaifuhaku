import fitz
path = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\kokusai_ningen\handbook2026_kokusai_ningen.pdf"
doc = fitz.open(path)
page = doc[132]
rect = page.rect
# Narrow crop just around 学科コア科目 row and the 必要修得単位数 column area
clip = fitz.Rect(rect.x0 + rect.width*0.0, rect.y0 + rect.height*0.585, rect.x1, rect.y0 + rect.height*0.66)
pix = page.get_pixmap(matrix=fitz.Matrix(8, 8), clip=clip)
out = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\kokusai_ningen\kokusai_p133_gakkacore_ultrahires.png"
pix.save(out)
print("saved", out)
