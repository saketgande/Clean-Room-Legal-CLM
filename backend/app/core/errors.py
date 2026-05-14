from fastapi import HTTPException, status


class AppError(HTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code=status_code, detail=detail)


class PermissionDenied(AppError):
    def __init__(self, detail: str = "Permission denied"):
        super().__init__(status.HTTP_403_FORBIDDEN, detail)


class NotFound(AppError):
    def __init__(self, detail: str = "Resource not found"):
        super().__init__(status.HTTP_404_NOT_FOUND, detail)


class ValidationFailed(AppError):
    def __init__(self, detail: str = "Validation failed"):
        super().__init__(status.HTTP_422_UNPROCESSABLE_ENTITY, detail)
