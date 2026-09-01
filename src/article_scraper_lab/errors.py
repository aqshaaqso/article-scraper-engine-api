"""Safe public error types."""


class UnsafeUrlError(ValueError):
    pass


class FetchError(RuntimeError):
    pass


class RobotsDeniedError(RuntimeError):
    pass


class ExtractionError(RuntimeError):
    pass


class BatchValidationError(ValueError):
    pass


class JobNotFoundError(LookupError):
    pass
