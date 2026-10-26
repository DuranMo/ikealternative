"""Small, sanitized error taxonomy shared by HTTP, metrics, and synchronization."""
from pydantic import ValidationError
from requests.exceptions import RequestException, Timeout, HTTPError
from .providers import NotFound, Unsupported, Unavailable


def category(exc: Exception) -> str:
    # Room failures retain their underlying cause for accurate instrumentation.
    if exc.__cause__ is not None:
        return category(exc.__cause__)
    if isinstance(exc, Timeout):
        return 'timeout'
    if isinstance(exc, HTTPError):
        status = exc.response.status_code if exc.response is not None else 0
        return 'connectivity' if status in (401, 403) or status >= 500 else 'command'
    if isinstance(exc, (RequestException, Unavailable)):
        return 'connectivity'
    if isinstance(exc, NotFound):
        return 'not_found'
    if isinstance(exc, (Unsupported, AssertionError)):
        return 'unsupported'
    if isinstance(exc, ValidationError):
        return 'invalid_hub_data'
    if isinstance(exc, ValueError):
        return 'invalid_input'
    return 'command'


def public_error(exc: Exception) -> tuple[int, str]:
    kind = category(exc)
    if isinstance(exc, Unavailable) and exc.__cause__ is not None:
        return 503, 'Group command failed; some lights may already have changed. Refresh before retrying.'
    messages = {
        'timeout': (503, 'DIRIGERA request timed out. State may be stale; reconnecting automatically.'),
        'connectivity': (503, 'Cannot communicate with DIRIGERA. Check hub power, local network, IP, token, and provider configuration.'),
        'not_found': (404, 'Light, room, or scene not found.'),
        'unsupported': (409, 'The hub or device does not support this operation.'),
        'invalid_hub_data': (502, 'DIRIGERA returned an unsupported device or scene format.'),
        'invalid_input': (422, 'The hub rejected the supplied command value.'),
        'command': (502, 'The hub command failed. Refresh state before retrying.'),
    }
    return messages[kind]
