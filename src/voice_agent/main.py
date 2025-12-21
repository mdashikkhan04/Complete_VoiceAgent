"""ASGI app entrypoint for the Voice Agent service.

This module exposes the FastAPI `app` object and includes a
minimal healthcheck endpoint used by orchestration tooling.
"""

from pydantic import BaseModel
from fastapi import FastAPI

# Import the webhooks router and mount it under /voice
from .api import webhooks as webhooks_router


# Pydantic model for the /health response to ensure a stable schema
class HealthResponse(BaseModel):
    status: str = "ok"


# Create and expose the FastAPI application object (ASGI app named `app`)
app = FastAPI(title="Voice Agent")

# Include the voice webhooks router at the /voice prefix
app.include_router(webhooks_router.router, prefix="/voice", tags=["voice"]) 


# Minimal health endpoint used for readiness/liveness checks
@app.get("/health", response_model=HealthResponse, status_code=200)
async def health() -> HealthResponse:
    """Return a simple health status in a predictable JSON schema."""
    return HealthResponse()
