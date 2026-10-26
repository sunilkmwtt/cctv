"""Uniform API error body: {"error": {"status": int, "code": str, "detail": ...}}."""

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler


class ServiceError(APIException):
    """API error with an explicit HTTP status and machine-readable code."""

    def __init__(self, detail: str, *, status_code: int, code: str):
        self.status_code = status_code
        super().__init__(detail=detail, code=code)


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, APIException):
        codes = exc.get_codes()
        code = codes if isinstance(codes, str) else "invalid"
    else:
        code = "error"
    if response.status_code == status.HTTP_404_NOT_FOUND:
        code = "not_found"
    detail = response.data
    if isinstance(detail, dict) and set(detail) == {"detail"}:
        detail = detail["detail"]  # plain message; field errors stay a dict
    response.data = {"error": {"status": response.status_code, "code": code, "detail": detail}}
    return response
