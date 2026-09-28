class ServiceError(Exception):
    """Something the person can act on, reported as {"detail": {"message", "field"?}}."""

    def __init__(self, message: str, *, status_code: int = 400, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.field = field


class NotFound(ServiceError):
    """Missing, or someone else's: the answer is the same, so ids can't be probed."""

    def __init__(self, what: str) -> None:
        super().__init__(f"{what} not found", status_code=404)
