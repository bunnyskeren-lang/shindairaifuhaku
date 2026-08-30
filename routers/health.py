import os

from fastapi import APIRouter

router = APIRouter()

# Renderはビルド・実行時に自動でRENDER_GIT_COMMIT(デプロイしたコミットのフルSHA)を
# 環境変数として注入するため、Render側への登録作業は不要。ローカル実行時は未設定のため
# "local" にフォールバックする(2026-08-31、以前はここに固定文字列のコミットハッシュを
# 手打ちしたまま更新し忘れており、実際のデプロイ内容と無関係な値を返し続けていた)
_VERSION = os.environ.get("RENDER_GIT_COMMIT", "local")[:7]


@router.get("/health")
@router.head("/health")
async def health():
    return {"status": "healthy", "version": _VERSION}
