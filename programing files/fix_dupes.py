import asyncio, ssl
import asyncpg

DELETE_IDS = [2409, 2410, 2367, 2394, 1845, 2423, 2362, 2355, 2359, 2354, 2404]

async def main():
    url = "postgresql://postgres.ofsvkcptzngbsxtdbqzj:Developerr6363st@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = await asyncpg.connect(url, ssl=ctx)

    # 削除前に確認表示
    rows = await conn.fetch(
        "SELECT id, course_id, name FROM course_instructors WHERE id = ANY($1::int[])",
        DELETE_IDS
    )
    print("削除対象:")
    for r in sorted(rows, key=lambda x: x["id"]):
        print(f"  id={r['id']} course_id={r['course_id']} name={repr(r['name'])}")

    result = await conn.execute(
        "DELETE FROM course_instructors WHERE id = ANY($1::int[])",
        DELETE_IDS
    )
    print(f"\n{result}")
    await conn.close()

asyncio.run(main())
