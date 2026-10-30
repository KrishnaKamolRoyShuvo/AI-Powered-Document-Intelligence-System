from fastapi import FastAPI

app = FastAPI(title="Document Intelligence API")


@app.get("/health")
def health():
    return {"status": "ok"}
