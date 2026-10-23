"""제공자 SDK에 의존하지 않는 제조 초안 오류. 예외 원문을 사용자/로그에 복사하지 않는다."""
from __future__ import annotations

from typing import Literal

FailureKind = Literal['timeout', 'dependency_missing', 'authentication', 'rate_limit',
                      'connection', 'invalid_response', 'provider_error']


class DraftGenerationError(RuntimeError):
    def __init__(self, stage: str, kind: FailureKind) -> None:
        self.stage = stage
        self.kind = kind
        super().__init__(f'{stage}: {kind}')


def classify_failure(exc: Exception) -> FailureKind:
    """표준 예외·SDK 타입 이름·HTTP 상태로 분류. 예외 메시지는 읽지 않는다."""
    names = {cls.__name__ for cls in type(exc).__mro__}
    if isinstance(exc, ImportError):
        return 'dependency_missing'
    if isinstance(exc, TimeoutError) or names & {'DeadlineExceeded', 'ReadTimeout', 'ConnectTimeout', 'APITimeoutError'}:
        return 'timeout'
    status = getattr(exc, 'status_code', None)
    if not isinstance(status, int):
        status = getattr(exc, 'code', None)
    if status in (401, 403) or names & {'Unauthorized', 'Unauthenticated', 'Forbidden', 'PermissionDenied', 'AuthenticationError'}:
        return 'authentication'
    if status == 429 or names & {'ResourceExhausted', 'TooManyRequests', 'RateLimitError'}:
        return 'rate_limit'
    if isinstance(exc, ConnectionError) or names & {'ServiceUnavailable', 'APIConnectionError', 'ConnectError'}:
        return 'connection'
    if isinstance(exc, (TypeError, ValueError)):
        return 'invalid_response'
    return 'provider_error'
