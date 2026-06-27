import asyncio, ssl
import asyncpg

# (course_id, name) のペアで削除（IDは環境によって異なる可能性があるため）
DELETE_PAIRS = [
    (499, '益喜 真子'),
    (499, '早田 互輝'),
    (352, '丸山 笑治'),
    (349, '西田 健弘'),
    (463, '川上紘史'),
    (463, '川上 拡史'),
    (370, '仲田 佐加'),
    (358, '南 佐亮'),
    (362, '寺内 義子'),
    (385, '葛城 済一'),
    (438, '嶋田 宏樹'),
    (361, '岸本 古弘'),
    (363, '上田 吾'),
    (407, '武賀 恭世'),
    (408, '江原 晴人'),
    (380, '中田 進也'),
    (384, '大川内 育'),
    (418, '金子 克彦'),
    (524, '三輪 泰之'),
    (547, '柴田 明稔'),
    (501, '三濦 靖史'),
    (501, '秋夫 敏宏'),
    (423, '岡邨 恭幸'),
    (432, '橋 伸也'),
    (418, '山崎 和仁'),
    (685, 'THOMAS,Julian'),
    (688, 'Chapman-S Ben'),
    (516, '新川 匹郎'),
]

async def main():
    url = "postgresql://postgres.sagubqrhjnzrtcvlmzqy:Linebot6363st@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = await asyncpg.connect(url, ssl=ctx)

    deleted = 0
    not_found = []
    for course_id, name in DELETE_PAIRS:
        row = await conn.fetchrow(
            "SELECT id FROM course_instructors WHERE course_id=$1 AND name=$2",
            course_id, name
        )
        if row:
            await conn.execute(
                "DELETE FROM course_instructors WHERE id=$1", row["id"]
            )
            print(f"  削除: course_id={course_id} name={repr(name)} id={row['id']}")
            deleted += 1
        else:
            not_found.append((course_id, name))

    print(f"\n削除完了: {deleted}件")
    if not_found:
        print(f"見つからなかった({len(not_found)}件):")
        for cid, n in not_found:
            print(f"  course_id={cid} name={repr(n)}")

    await conn.close()

asyncio.run(main())
