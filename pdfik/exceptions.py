from typing import Optional

class PdfikError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int,
        error_code: Optional[str] = None,
        response_body: Optional[str] = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.response_body = response_body

    def __str__(self):
        code_str = f" [{self.error_code}]" if self.error_code else ""
        return f"{self.args[0]} (HTTP {self.status_code}){code_str}"
