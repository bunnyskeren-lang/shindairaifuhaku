import asyncio, re, ssl, unicodedata
import asyncpg

def levenshtein(a, b):
    if len(a) < len(b):
        return levenshtein(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]

def normalize(s):
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\s　 ]+", "", s)
    return s.lower()

async def main():
    url = "postgresql://postgres.ofsvkcptzngbsxtdbqzj:Developerr6363st@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = await asyncpg.connect(url, ssl=ctx)

    rows = await conn.fetch("""
        SELECT ci.course_id, c.name as course_name,
               array_agg(ci.id ORDER BY ci.id) as ids,
               array_agg(ci.name ORDER BY ci.id) as instructors
        FROM course_instructors ci
        JOIN courses c ON c.id = ci.course_id
        GROUP BY ci.course_id, c.name
        HAVING count(*) > 1
        ORDER BY c.name
    """)

    out = []
    out.append(f"複数先生がいるコース数: {len(rows)}\n")
    out.append("=== 疑わしいペア（編集距離 <= 2）===\n")

    found = 0
    for row in rows:
        instructors = list(row["instructors"])
        ids = list(row["ids"])
        normed = [normalize(i) for i in instructors]

        pairs = []
        for i in range(len(instructors)):
            for j in range(i + 1, len(instructors)):
                n1, n2 = normed[i], normed[j]
                dist = levenshtein(n1, n2)
                if dist <= 2:
                    pairs.append((dist, ids[i], ids[j], instructors[i], instructors[j]))

        if pairs:
            found += 1
            out.append(f"[{row['course_id']}] {row['course_name']}")
            for dist, id1, id2, a, b in sorted(pairs):
                marker = "完全一致" if dist == 0 else f"距離{dist}"
                out.append(f"  ({marker}) id={id1}: {repr(a)}")
                out.append(f"            id={id2}: {repr(b)}")
            out.append("")

    out.append(f"疑わしいペアがあるコース数: {found}")

    with open("dupe_check.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out))

    print("Done.")
    await conn.close()

asyncio.run(main())
