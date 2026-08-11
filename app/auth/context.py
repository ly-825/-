from contextvars import ContextVar

from app.models import Account


current_account: ContextVar[Account | None] = ContextVar(
    "current_account",
    default=None,
)


def get_current_account() -> Account | None:
    return current_account.get()
