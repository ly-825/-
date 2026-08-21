from fastapi import HTTPException, status

from app.auth.context import get_current_account


def verified_operator_name() -> str:
    account = get_current_account()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
        )
    name = account.display_name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前账号未设置姓名，不能执行库存操作",
        )
    return name
