from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, SessionLocal, engine
from .api import bridge, chapters, projects, providers, role_assignments, worldbuild, rules, foreshadowings, ai_assist
from .api.role_assignments import seed_role_assignments

Base.metadata.create_all(bind=engine)

app = FastAPI(title="AI小说写作助手 - 原型后端")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 原型阶段先放开，部署前收紧
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(worldbuild.router)
app.include_router(chapters.router)
app.include_router(providers.router)
app.include_router(role_assignments.router)
app.include_router(bridge.router)
app.include_router(rules.router)
app.include_router(foreshadowings.router)
app.include_router(ai_assist.router)


@app.on_event("startup")
def startup_seed_role_assignments():
    db = SessionLocal()
    try:
        seed_role_assignments(db)
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}
