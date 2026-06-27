import asyncio, ssl
import asyncpg

DEV_URL  = "postgresql://postgres.ofsvkcptzngbsxtdbqzj:Developerr6363st@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"
PROD_URL = "postgresql://postgres.sagubqrhjnzrtcvlmzqy:Linebot6363st@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres"

def ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

async def main():
    dev  = await asyncpg.connect(DEV_URL,  ssl=ssl_ctx())
    prod = await asyncpg.connect(PROD_URL, ssl=ssl_ctx())

    # --- classification_orders ---
    cls_rows = await dev.fetch("SELECT * FROM classification_orders ORDER BY id")
    async with prod.transaction():
        await prod.execute("DELETE FROM classification_orders")
        if cls_rows:
            await prod.executemany(
                "INSERT INTO classification_orders (id, name, sort_order) VALUES ($1, $2, $3)",
                [(r["id"], r["name"], r["sort_order"]) for r in cls_rows]
            )
    print(f"classification_orders: {len(cls_rows)}件")

    # --- courses ---
    course_rows = await dev.fetch("SELECT * FROM courses ORDER BY id")
    async with prod.transaction():
        await prod.execute("DELETE FROM course_instructors")
        await prod.execute("DELETE FROM courses")
        if course_rows:
            await prod.executemany(
                """INSERT INTO courses
                   (id, name, instructor, classification, category, syllabus_url, reading, sort_order, term, credits)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                [(r["id"], r["name"], r["instructor"], r["classification"],
                  r["category"], r["syllabus_url"], r["reading"], r["sort_order"],
                  r.get("term"), r.get("credits"))
                 for r in course_rows]
            )
    print(f"courses: {len(course_rows)}件")

    # --- course_instructors ---
    ci_rows = await dev.fetch("SELECT * FROM course_instructors ORDER BY id")
    async with prod.transaction():
        if ci_rows:
            await prod.executemany(
                "INSERT INTO course_instructors (id, course_id, name, url) VALUES ($1,$2,$3,$4)",
                [(r["id"], r["course_id"], r["name"], r.get("url")) for r in ci_rows]
            )
    print(f"course_instructors: {len(ci_rows)}件")

    await dev.close()
    await prod.close()
    print("\nDB同期完了")

asyncio.run(main())
