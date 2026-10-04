"""Types for request input that must be refused before it reaches PostgreSQL.

PostgreSQL cannot store a NUL character in text and its id columns are 32-bit, so either one
would otherwise end as a 500 instead of a 422.
"""

from typing import Annotated

from pydantic import AfterValidator, Field

DB_INT_MAX = 2**31 - 1


def _reject_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


Text = Annotated[str, AfterValidator(_reject_nul)]
DbId = Annotated[int, Field(ge=0, le=DB_INT_MAX)]
