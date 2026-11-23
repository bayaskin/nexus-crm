from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.models.qa import QARun
from app.models.github import GitHubPRRevision, GitHubPullRequestState, GitHubWebhookDelivery
from app.schemas.github import GitHubChangedFile, GitHubPRContext
from app.schemas.qa import CreateQARunRequest, QARunError, QARunState, QARunStatus, QATriggerType
from app.services.github_client import GitHubAPIError, GitHubPullRequestClient, GitHubRateLimited
from app.services.qa_orchestrator import ACTIVE_STATUSES, QARunOrchestrator


MAX_WEBHOOK_BYTES = 1_048_576
SUPPORTED_ACTIONS = {"opened", "reopened", "synchronize"}


class GitHubWebhookError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class WebhookOwner(BaseModel):
    model_config = ConfigDict(extra="ignore")
    login: str = Field(min_length=1, max_length=100)


class WebhookRepository(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=100)
    full_name: str = Field(min_length=3, max_length=255)
    owner: WebhookOwner


class WebhookRevision(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ref: str = Field(min_length=1, max_length=255)
    sha: str = Field(pattern=r"^[0-9a-fA-F]{40}$")


class WebhookPullRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    number: int = Field(gt=0)
    head: WebhookRevision
    base: WebhookRevision
    updated_at: datetime


class PullRequestWebhook(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: str = Field(min_length=1, max_length=64)
    repository: WebhookRepository
    pull_request: WebhookPullRequest


@dataclass(frozen=True)
class IngestionOutcome:
    run: QARun | None
    created: bool
    ignored: bool = False
    start_execution: bool = False


class GitHubPRIngestionService:
    def __init__(
        self,
        session_factory,
        orchestrator: QARunOrchestrator,
        *,
        configuration: Settings = settings,
        client: GitHubPullRequestClient | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.orchestrator = orchestrator
        self.configuration = configuration
        token = configuration.github_token.get_secret_value() if configuration.github_token else ""
        self.client = client or GitHubPullRequestClient(
            token,
            timeout=configuration.github_api_request_timeout_seconds,
            deadline_seconds=configuration.github_ingestion_timeout_seconds,
            max_retries=configuration.github_api_max_retries,
            max_response_bytes=configuration.github_api_max_response_bytes,
        )

    def ingest(
        self,
        body: bytes,
        *,
        signature: str | None,
        event_name: str | None,
        delivery_id: str | None,
    ) -> IngestionOutcome:
        webhook_secret = self.configuration.github_webhook_secret
        if webhook_secret is None or not webhook_secret.get_secret_value():
            raise GitHubWebhookError(503, "GitHub webhook integration is not configured")
        if len(body) > MAX_WEBHOOK_BYTES:
            raise GitHubWebhookError(413, "GitHub webhook payload exceeds the permitted size")
        if not self.verify_signature(body, signature, webhook_secret.get_secret_value()):
            raise GitHubWebhookError(401, "Invalid GitHub webhook signature")
        if not event_name or not delivery_id or len(delivery_id) > 128:
            raise GitHubWebhookError(400, "GitHub event and delivery headers are required")
        if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in delivery_id):
            raise GitHubWebhookError(400, "Invalid GitHub delivery identifier")
        if event_name != "pull_request":
            return IngestionOutcome(run=None, created=False, ignored=True)
        if not self.configuration.github_allowed_repositories:
            raise GitHubWebhookError(503, "No GitHub repositories are configured for ingestion")

        try:
            payload = PullRequestWebhook.model_validate_json(body)
        except ValidationError as exc:
            raise GitHubWebhookError(400, "Malformed GitHub pull request event") from exc
        if payload.action not in SUPPORTED_ACTIONS:
            return IngestionOutcome(run=None, created=False, ignored=True)

        repository_name = payload.repository.full_name
        if repository_name.casefold() not in self.configuration.allowed_github_repositories:
            raise GitHubWebhookError(403, "This GitHub repository is not configured for ingestion")
        try:
            owner, name = self.client.validate_repository_name(repository_name)
        except ValueError as exc:
            raise GitHubWebhookError(400, "Invalid GitHub repository identity") from exc
        if payload.repository.full_name.casefold() != f"{payload.repository.owner.login}/{payload.repository.name}".casefold():
            raise GitHubWebhookError(400, "Malformed GitHub repository identity")

        # Reserve a monotonic request sequence in a short transaction. All network
        # calls happen after closing this session; the sequence makes overlapping
        # webhook work deterministic even when event timestamps are equal/stale.
        key = (payload.repository.id, payload.pull_request.number)
        db = self.session_factory()
        try:
            prior_delivery = db.get(GitHubWebhookDelivery, delivery_id)
            if prior_delivery is not None:
                self._assert_delivery_record_matches(prior_delivery, payload)
                run = db.get(QARun, prior_delivery.qa_run_id) if prior_delivery.qa_run_id else None
                return IngestionOutcome(run=run, created=False, ignored=run is None,
                    start_execution=bool(run and run.status == QARunStatus.PENDING.value and not run.cancel_requested))
            duplicate = self._find_delivery(db, delivery_id, payload)
            if duplicate is not None:
                return IngestionOutcome(run=duplicate, created=False,
                                        start_execution=duplicate.status == QARunStatus.PENDING.value and not duplicate.cancel_requested)
            try:
                row = self._lock_pull_request_state(db, payload.repository.id, payload.pull_request.number,
                                                   payload.pull_request.head.sha.lower(), payload.pull_request.updated_at)
            except IntegrityError:
                # Another transaction may have inserted the state row at the same
                # time. Re-read it under lock after rolling back the losing insert.
                db.rollback()
                row = db.scalar(select(GitHubPullRequestState).where(
                    GitHubPullRequestState.repository_id == payload.repository.id,
                    GitHubPullRequestState.pull_request_number == payload.pull_request.number,
                ).with_for_update())
                if row is None:
                    raise GitHubWebhookError(503, "GitHub event could not reserve a pull request sequence")
            sequence = row.next_request_sequence
            row.next_request_sequence += 1
            db.commit()
        except IntegrityError:
            db.rollback()
            raise GitHubWebhookError(503, "GitHub event could not be reserved; retry delivery")
        finally:
            db.close()

        if not self.configuration.github_token or not self.configuration.github_token.get_secret_value():
            raise GitHubWebhookError(503, "GitHub API access is not configured")
        try:
            pull_request, changed_files = self.client.fetch_pull_request(f"{owner}/{name}", payload.pull_request.number)
            api_head = pull_request.get("head", {}).get("sha", "").lower()
            if api_head != payload.pull_request.head.sha.lower():
                return self._record_ignored_delivery(delivery_id, payload, "ignored_stale")
            context = self._normalize_context(pull_request, changed_files, payload,
                                              event_name=payload.action, delivery_id=delivery_id)
            context.reported_changed_files = pull_request["changed_files"]
        except GitHubRateLimited as exc:
            raise GitHubWebhookError(429, "GitHub API rate limit exceeded; retry later") from exc
        except GitHubAPIError as exc:
            if exc.status_code == 409:
                return self._record_ignored_delivery(delivery_id, payload, "ignored_stale")
            raise GitHubWebhookError(exc.status_code, str(exc)) from exc
        except GitHubWebhookError:
            raise
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            raise GitHubWebhookError(502, "GitHub returned invalid pull request metadata") from exc

        db = self.session_factory()
        try:
            row = db.scalar(select(GitHubPullRequestState).where(
                GitHubPullRequestState.repository_id == key[0],
                GitHubPullRequestState.pull_request_number == key[1]).with_for_update().execution_options(populate_existing=True))
            prior_delivery = db.get(GitHubWebhookDelivery, delivery_id)
            if prior_delivery is not None:
                self._assert_delivery_record_matches(prior_delivery, payload)
                prior_run = db.get(QARun, prior_delivery.qa_run_id) if prior_delivery.qa_run_id else None
                return IngestionOutcome(run=prior_run, created=False, ignored=prior_run is None,
                    start_execution=bool(prior_run and prior_run.status == QARunStatus.PENDING.value and not prior_run.cancel_requested))
            duplicate = self._find_delivery(db, delivery_id, payload)
            if duplicate is not None:
                return IngestionOutcome(run=duplicate, created=False,
                                        start_execution=duplicate.status == QARunStatus.PENDING.value and not duplicate.cancel_requested)
            # An older request completing after a newer observation is stale.
            if sequence < row.latest_observation_sequence and (
                row.latest_head_sha != context.head_sha or row.latest_base_sha != context.base_sha
            ):
                self._record_delivery_fields(db, delivery_id, context.repository_id, context.pull_request_number,
                                             context.event_base_sha or context.base_sha, context.head_sha,
                                             context.event_type, None, "ignored_stale")
                db.commit()
                return IngestionOutcome(run=None, created=False, ignored=True)
            # A newer duplicate observation of the current head still advances
            # the watermark, preventing an older in-flight different head from
            # replacing it when that older HTTP request finishes last.
            row.latest_observation_sequence = max(row.latest_observation_sequence, sequence)
            current_revision = None
            if row.latest_head_sha == context.head_sha and row.latest_base_sha == context.base_sha:
                current_revision = db.scalar(select(GitHubPRRevision).where(
                    GitHubPRRevision.repository_id == context.repository_id,
                    GitHubPRRevision.pull_request_number == context.pull_request_number,
                    GitHubPRRevision.head_sha == context.head_sha,
                    GitHubPRRevision.base_sha == context.base_sha,
                ).order_by(GitHubPRRevision.observed_sequence.desc()))
            is_same_current = (
                row.latest_head_sha == context.head_sha
                and row.latest_base_sha == context.base_sha
                and current_revision is not None
            )
            if is_same_current:
                prior = db.scalar(select(QARun).where(QARun.github_revision_id == current_revision.id))
                if prior is not None:
                    context.revision_id = current_revision.id
                    self._record_delivery(db, context, prior.id)
                    db.commit()
                    return IngestionOutcome(run=prior, created=False,
                        start_execution=prior.status == QARunStatus.PENDING.value and not prior.cancel_requested)
            if not is_same_current:
                context.revision_id = str(uuid4())
                revision = GitHubPRRevision(id=context.revision_id, repository_id=context.repository_id,
                    pull_request_number=context.pull_request_number, base_sha=context.base_sha,
                    head_sha=context.head_sha, observed_sequence=sequence,
                    created_at=self._event_time(self.orchestrator.clock()))
                db.add(revision)
                row.latest_head_sha = context.head_sha
                row.latest_base_sha = context.base_sha
                row.latest_event_at = self._event_time(context.event_updated_at)
            else:
                context.revision_id = current_revision.id
            run = self.orchestrator.create(db, CreateQARunRequest(), None, github_context=context,
                trigger_type=QATriggerType.GITHUB_PULL_REQUEST, commit=False)
            self._record_delivery(db, context, run.id)
            self._supersede_older_active_runs(db, context, except_run_id=run.id, completed_at=self.orchestrator.clock())
            db.commit()
            db.refresh(run)
            return IngestionOutcome(run=run, created=True, start_execution=True)
        except IntegrityError:
            db.rollback()
            duplicate = db.get(GitHubWebhookDelivery, delivery_id)
            if duplicate:
                run = db.get(QARun, duplicate.qa_run_id) if duplicate.qa_run_id else None
                return IngestionOutcome(run=run, created=False, ignored=run is None,
                    start_execution=bool(run and run.status == QARunStatus.PENDING.value and not run.cancel_requested))
            raise GitHubWebhookError(409, "GitHub delivery conflicts with an existing QA run")
        finally:
            db.close()

    @staticmethod
    def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
        if not signature or not signature.startswith("sha256=") or len(signature) != 71:
            return False
        expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def _assert_delivery_matches(run: QARun, payload: PullRequestWebhook, delivery_id: str) -> None:
        if (
            run.github_delivery_id != delivery_id
            or run.github_repository_id != payload.repository.id
            or run.github_pr_number != payload.pull_request.number
            or (run.github_context or {}).get("event_base_sha", (run.github_context or {}).get("base_sha")) != payload.pull_request.base.sha.lower()
            or run.github_head_sha != payload.pull_request.head.sha.lower()
        ):
            raise GitHubWebhookError(409, "GitHub delivery identifier was reused for a different revision")

    @staticmethod
    def _assert_delivery_record_matches(delivery: GitHubWebhookDelivery, payload: PullRequestWebhook) -> None:
        if (delivery.repository_id != payload.repository.id
            or delivery.pull_request_number != payload.pull_request.number
            or delivery.base_sha != payload.pull_request.base.sha.lower()
            or delivery.head_sha != payload.pull_request.head.sha.lower()
            or delivery.event_type != payload.action):
            raise GitHubWebhookError(409, "GitHub delivery identifier was reused for a different revision")

    def _find_delivery(self, db: Session, delivery_id: str, payload: PullRequestWebhook) -> QARun | None:
        delivery = db.get(GitHubWebhookDelivery, delivery_id)
        if delivery is None:
            # Compatibility for rows created before delivery records were added.
            run = db.scalar(select(QARun).where(QARun.github_delivery_id == delivery_id))
            if run is None:
                return None
            self._assert_delivery_matches(run, payload, delivery_id)
            return run
        if (
            delivery.repository_id != payload.repository.id
            or delivery.pull_request_number != payload.pull_request.number
            or delivery.base_sha != payload.pull_request.base.sha.lower()
            or delivery.head_sha != payload.pull_request.head.sha.lower()
            or delivery.event_type != payload.action
        ):
            raise GitHubWebhookError(409, "GitHub delivery identifier was reused for a different revision")
        run = db.get(QARun, delivery.qa_run_id) if delivery.qa_run_id else None
        if run is None:
            return None
        return run

    def _record_delivery(self, db: Session, context: GitHubPRContext, run_id) -> None:
        self._record_delivery_fields(
            db,
            context.delivery_id,
            context.repository_id,
            context.pull_request_number,
            context.event_base_sha or context.base_sha,
            context.head_sha,
            context.event_type,
            run_id,
        )

    def _record_delivery_for_event(self, db: Session, delivery_id: str, payload: PullRequestWebhook, run_id) -> None:
        self._record_delivery_fields(
            db,
            delivery_id,
            payload.repository.id,
            payload.pull_request.number,
            payload.pull_request.base.sha.lower(),
            payload.pull_request.head.sha.lower(),
            payload.action,
            run_id,
        )

    def _record_delivery_fields(
        self,
        db: Session,
        delivery_id: str,
        repository_id: int,
        pull_request_number: int,
        base_sha: str,
        head_sha: str,
        event_type: str,
        run_id,
        disposition: str = "accepted",
    ) -> None:
        db.add(
            GitHubWebhookDelivery(
                delivery_id=delivery_id,
                repository_id=repository_id,
                pull_request_number=pull_request_number,
                base_sha=base_sha,
                head_sha=head_sha,
                event_type=event_type,
                qa_run_id=run_id,
                disposition=disposition,
                received_at=self._event_time(self.orchestrator.clock()),
            )
        )

    def _record_ignored_delivery(self, delivery_id: str, payload: PullRequestWebhook, disposition: str) -> IngestionOutcome:
        db = self.session_factory()
        try:
            existing = db.get(GitHubWebhookDelivery, delivery_id)
            if existing is not None:
                self._assert_delivery_record_matches(existing, payload)
                run = db.get(QARun, existing.qa_run_id) if existing.qa_run_id else None
                return IngestionOutcome(run=run, created=False, ignored=run is None)
            self._record_delivery_fields(db, delivery_id, payload.repository.id, payload.pull_request.number,
                payload.pull_request.base.sha.lower(), payload.pull_request.head.sha.lower(), payload.action,
                None, disposition)
            db.commit()
            return IngestionOutcome(run=None, created=False, ignored=True)
        finally:
            db.close()

    @staticmethod
    def _lock_pull_request_state(db: Session, repository_id: int, pull_request_number: int,
                                 head_sha: str, event_updated_at: datetime) -> GitHubPullRequestState:
        key = (repository_id, pull_request_number)
        state_row = db.get(GitHubPullRequestState, key)
        if state_row is None:
            state_row = GitHubPullRequestState(
                repository_id=repository_id,
                pull_request_number=pull_request_number,
                latest_head_sha=None,
                latest_base_sha=None,
                latest_event_at=None,
                next_request_sequence=1,
                latest_observation_sequence=0,
            )
            db.add(state_row)
            db.flush()
        return db.scalar(
            select(GitHubPullRequestState)
            .where(
                GitHubPullRequestState.repository_id == repository_id,
                GitHubPullRequestState.pull_request_number == pull_request_number,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    @staticmethod
    def _event_time(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _normalize_context(
        pull_request: dict[str, Any],
        changed_files: list[dict[str, Any]],
        payload: PullRequestWebhook,
        *,
        event_name: str,
        delivery_id: str,
    ) -> GitHubPRContext:
        base = pull_request["base"]
        head = pull_request["head"]
        repository = base["repo"]
        repository_full_name = repository["full_name"]
        expected_revision = payload.pull_request
        if (
            int(repository["id"]) != payload.repository.id
            or repository_full_name.casefold() != payload.repository.full_name.casefold()
            or int(pull_request["number"]) != expected_revision.number
            or head["sha"].lower() != expected_revision.head.sha.lower()
        ):
            raise GitHubWebhookError(409, "Pull request changed after this webhook event was created")
        owner = repository["owner"]["login"]
        name = repository["name"]
        merge_sha = pull_request.get("merge_commit_sha")
        context = GitHubPRContext(
            repository_id=int(repository["id"]),
            repository_full_name=repository_full_name,
            repository_owner=owner,
            repository_name=name,
            default_branch=repository.get("default_branch"),
            pull_request_number=int(pull_request["number"]),
            title=pull_request["title"],
            url=pull_request["html_url"],
            author=pull_request["user"]["login"],
            state=pull_request["state"],
            draft=bool(pull_request["draft"]),
            source_branch=head["ref"],
            target_branch=base["ref"],
            base_sha=base["sha"],
            event_base_sha=expected_revision.base.sha,
            head_sha=expected_revision.head.sha,
            merge_sha=merge_sha,
            changed_files=[
                GitHubChangedFile(
                    path=file["filename"],
                    status=file["status"],
                    additions=file["additions"],
                    deletions=file["deletions"],
                    changes=file["changes"],
                    previous_filename=file.get("previous_filename"),
                )
                for file in changed_files
            ],
            reported_changed_files=pull_request["changed_files"],
            event_type=event_name,
            delivery_id=delivery_id,
            event_updated_at=expected_revision.updated_at,
        )
        return context

    @staticmethod
    def _supersede_older_active_runs(
        db: Session,
        context: GitHubPRContext,
        *,
        except_run_id,
        completed_at: datetime,
    ) -> None:
        older_runs = db.scalars(
            select(QARun).where(
                QARun.github_repository_id == context.repository_id,
                QARun.github_pr_number == context.pull_request_number,
                QARun.github_revision_id != context.revision_id,
                QARun.status.in_([status.value for status in ACTIVE_STATUSES]),
                QARun.id != except_run_id,
            )
            .with_for_update()
        ).all()
        for old_run in older_runs:
            old_run.status = QARunStatus.SUPERSEDED.value
            old_run.completed_at = old_run.completed_at or completed_at
            if old_run.started_at:
                old_run.duration_ms = max(0, int((old_run.completed_at - old_run.started_at).total_seconds() * 1000))
            old_run.error = QARunError(
                code="superseded",
                message="A newer pull request revision was received.",
            ).model_dump(mode="json")
            if old_run.state_data:
                state = QARunState.model_validate(old_run.state_data)
                state.status = QARunStatus.SUPERSEDED
                old_run.state_data = state.model_dump(mode="json")
