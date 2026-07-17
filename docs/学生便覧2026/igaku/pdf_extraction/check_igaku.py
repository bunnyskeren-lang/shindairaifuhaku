with open(r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\igaku\igaku_full.txt", encoding="utf-8") as f:
    content = f.read()

with open(r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\igaku\igaku_check_out.txt", "w", encoding="utf-8") as f:
    f.write(f"total length: {len(content)}\n")
    f.write("---- first 3000 chars ----\n")
    f.write(content[:3000])
    f.write("\n---- page 10 sample ----\n")
    idx = content.find("===== PAGE 10 =====")
    f.write(content[idx:idx+2000])
