import fitz
path = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\kokusai_ningen\handbook2026_kokusai_ningen.pdf"
doc = fitz.open(path)
print("total pages", len(doc))
targets = [183, 188, 194, 200, 201]
for t in targets:
    idx = t - 1  # assume PAGE label == 1-indexed pdf page
    page = doc[idx]
    pix = page.get_pixmap(matrix=fitz.Matrix(2.2, 2.2))
    out = rf"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\kokusai_ningen\kokusai_haitou_p{t}.png"
    pix.save(out)
    print("saved", out)
