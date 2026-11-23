import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app.api.routes.qa import get_qa_orchestrator
from app.core.database import SessionLocal
from app.schemas.github import GitHubWebhookResponse
from app.services.github_ingestion import MAX_WEBHOOK_BYTES, GitHubPRIngestionService, GitHubWebhookError
from app.services.qa_orchestrator import QARunOrchestrator


router = APIRouter(prefix="/v1/integrations/github", tags=["GitHub integration"])


def get_github_ingestion_service(
    orchestrator: QARunOrchestrator = Depends(get_qa_orchestrator),
) -> GitHubPRIngestionService:
    return GitHubPRIngestionService(SessionLocal, orchestrator)


@router.post("/webhook", response_model=GitHubWebhookResponse, status_code=202)
async def receive_github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    service: GitHubPRIngestionService = Depends(get_github_ingestion_service),
    orchestrator: QARunOrchestrator = Depends(get_qa_orchestrator),
) -> GitHubWebhookResponse:
    content_length = request.headers.get("Content-Length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="GitHub webhook payload exceeds the permitted size")
    body_parts: list[bytes] = []
    body_length = 0
    async for part in request.stream():
        body_length += len(part)
        if body_length > MAX_WEBHOOK_BYTES:
            raise HTTPException(status_code=413, detail="GitHub webhook payload exceeds the permitted size")
        body_parts.append(part)
    body = b"".join(body_parts)
    try:
        outcome = await asyncio.to_thread(
            service.ingest,
            body,
            signature=request.headers.get("X-Hub-Signature-256"),
            event_name=request.headers.get("X-GitHub-Event"),
            delivery_id=request.headers.get("X-GitHub-Delivery"),
        )
    except GitHubWebhookError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    if outcome.start_execution and outcome.run is not None:
        background_tasks.add_task(orchestrator.execute, outcome.run.id)
    return GitHubWebhookResponse(
        status="ignored" if outcome.ignored else "accepted",
        duplicate=outcome.run is not None and not outcome.created,
        run_id=outcome.run.id if outcome.run is not None else None,
    )
