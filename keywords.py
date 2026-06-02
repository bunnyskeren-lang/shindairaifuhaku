from courses import COURSES

KEYWORD_RULES: list[dict] = [
    {
        "keywords": ["科目一覧", "科目", "授業一覧"],
        "reply": "科目一覧",
        "action": "course_list",
    },
    {
        "keywords": ["こんにちは", "おはよう", "hello", "hi"],
        "reply": "こんにちは！ご用件をお選びください",
        "buttons": [
            {"label": "📚 科目一覧", "text": "科目一覧"},
            {"label": "🕐 営業時間", "text": "営業時間"},
            {"label": "📅 予約する", "text": "予約"},
        ],
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
        "keywords": ["ヘルプ", "help", "使い方", "メニュー"],
        "reply": "ご用件をお選びください",
        "buttons": [
            {"label": "📚 科目一覧", "text": "科目一覧"},
            {"label": "🕐 営業時間", "text": "営業時間"},
            {"label": "📅 予約する", "text": "予約"},
            {"label": "💰 料金プラン", "text": "料金"},
        ],
    },
    {
        "keywords": ["写真", "画像", "フォト"],
        "reply": "こちらをご覧ください",
        "image_url": "https://placehold.co/1024x768.png",
    },
]

DEFAULT_REPLY = "申し訳ございません、よく理解できませんでした。「ヘルプ」と送ると使い方をご案内します。"


def get_rule(text: str) -> dict | None:
    if text in COURSES:
        return {"action": "course_detail", "course_name": text}
    normalized = text.lower()
    for rule in KEYWORD_RULES:
        if any(kw.lower() in normalized for kw in rule["keywords"]):
            return rule
    return None
