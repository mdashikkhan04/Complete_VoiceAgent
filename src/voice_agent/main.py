"""FastAPI app entrypoint (placeholder)."""

from fastapi import FastAPI

app = FastAPI(title="Voice Agent")


@app.get("/health")
async def health():
    """Simple healthcheck endpoint."""
    return {"status": "ok"}
