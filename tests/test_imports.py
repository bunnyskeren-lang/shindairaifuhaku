import importlib

import pytest

MODULES = [
    "database",
    "models",
    "core.config",
    "core.security",
    "core.cache",
    "core.activity_log",
    "core.line_client",
    "core.push",
    "core.mail",
    "core.prewarm",
    "core.templates",
    "core.backup",
    "line_bot.flex_builders",
    "line_bot.handler",
    "routers.webhook",
    "routers.health",
    "routers.pages",
    "routers.richmenu",
    "routers.liff_api",
    "routers.profile_api",
    "routers.review_submit_api",
    "routers.email_verify_api",
    "routers.admin.auth",
    "routers.admin.dashboard",
    "routers.admin.courses",
    "routers.admin.instructors",
    "routers.admin.classifications",
    "routers.admin.reviews",
    "routers.admin.users_errors",
    "routers.admin.stats",
    "main",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name):
    importlib.import_module(module_name)
