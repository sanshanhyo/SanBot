from __future__ import annotations


class JavLibraryError(Exception):
    error_code = "JAVLIBRARY_ERROR"

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class JavLibraryValidationError(JavLibraryError):
    error_code = "JAV_CODE_INVALID"


class JavLibraryNotFoundError(JavLibraryError):
    error_code = "JAV_NOT_FOUND"


class JavLibraryBlockedError(JavLibraryError):
    error_code = "JAV_SOURCE_BLOCKED"


class JavLibraryTimeoutError(JavLibraryError):
    error_code = "JAV_FETCH_TIMEOUT"


class JavLibraryFetchError(JavLibraryError):
    error_code = "JAV_FETCH_FAILED"


class JavLibraryParseError(JavLibraryError):
    error_code = "JAV_PARSE_FAILED"
