from fastapi import FastAPI

from level_tester.settings import get_settings


settings = get_settings()
app = FastAPI(title="Level Tester", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}
