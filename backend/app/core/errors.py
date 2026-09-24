"""Domain exception hierarchy + FastAPI exception handlers."""


class DomainError(Exception):
    status_code = 400
    code = "DOMAIN_ERROR"

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationFailed(DomainError):
    status_code = 422
    code = "VALIDATION_FAILED"


class NotFound(DomainError):
    status_code = 404
    code = "NOT_FOUND"


class PermissionDenied(DomainError):
    status_code = 403
    code = "PERMISSION_DENIED"


class SoDError(DomainError):
    """Segregation-of-duties violation (Part 1)."""
    status_code = 403
    code = "SOD_VIOLATION"


class WorkflowError(DomainError):
    status_code = 409
    code = "WORKFLOW_ERROR"


class PolicyViolation(DomainError):
    status_code = 409
    code = "POLICY_VIOLATION"


class IdempotencyConflict(DomainError):
    status_code = 409
    code = "IDEMPOTENCY_CONFLICT"


class ConflictError(DomainError):
    status_code = 409
    code = "CONFLICT"


def register_handlers(app):
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": exc.message, "details": exc.details},
        )
