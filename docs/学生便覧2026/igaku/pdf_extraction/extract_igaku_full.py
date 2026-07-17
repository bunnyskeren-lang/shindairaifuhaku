import pdfplumber

pdf_path = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\igaku\binran_2026_igaku.pdf"
out_path = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\igaku\igaku_full.txt"

with pdfplumber.open(pdf_path) as pdf:
    with open(out_path, "w", encoding="utf-8") as f:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            f.write(f"\n===== PAGE {i+1} =====\n")
            f.write(text)
            f.write("\n")

print("done", len(pdf.pages))
