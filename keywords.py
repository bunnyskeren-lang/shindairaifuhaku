KEYWORD_RULES: list[dict] = [
    {
        "keywords": ["こんにちは", "おはよう", "hello", "hi"],
        "reply": "こんにちは！何かご用件はありますか？",
    },
    {
        "keywords": ["ありがとう", "thanks", "thank you"],
        "reply": "どういたしまして！またいつでもどうぞ😊",
    },
    {
        "keywords": ["営業時間", "営業"],
        "reply": "営業時間は平日 9:00〜18:00 です。",
    },
    {
        "keywords": ["予約", "申し込み"],
        "reply": "ご予約はこちらのフォームからどうぞ：https://example.com/reservation",
    },
    {
        "keywords": ["価格", "料金", "いくら"],
        "reply": "料金プランはこちらをご確認ください：https://example.com/pricing",
    },
    {
        "keywords": ["ヘルプ", "help", "使い方"],
        "reply": (
            "以下のキーワードで話しかけてください：\n"
            "・「営業時間」→ 営業時間を案内\n"
            "・「予約」→ 予約フォームを案内\n"
            "・「料金」→ 料金プランを案内"
        ),
    },
]

DEFAULT_REPLY = "申し訳ございません、よく理解できませんでした。「ヘルプ」と送ると使い方をご案内します。"


def get_reply(text: str) -> str:
    normalized = text.lower()
    for rule in KEYWORD_RULES:
        if any(kw.lower() in normalized for kw in rule["keywords"]):
            return rule["reply"]
    return DEFAULT_REPLY
