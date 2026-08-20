import html

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.admin_pages import page
from app.auth.dependencies import require_pc_admin_account
from app.auth.roles import EMPLOYEE, OWNER, SUPERADMIN, can_manage_role
from app.auth.service import (
    create_managed_account,
    disable_account,
    enable_account,
    regenerate_activation,
    unbind_wechat,
)
from app.database import get_db
from app.models import Account
from app.services.operation_log import record_operation_log


router = APIRouter()


def managed_account(db: Session, actor: Account, account_id: int) -> Account:
    target = db.get(Account, account_id)
    if target is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if not can_manage_role(actor.role, target.role):
        raise HTTPException(status_code=403, detail="无权管理该账号")
    return target


def _audit_data(account: Account) -> dict[str, object]:
    return {
        "account_id": account.id,
        "role": account.role,
        "username": account.username,
        "is_active": account.is_active,
        "wechat_bound": bool(account.wechat_openid),
    }


def _role_label(role: str) -> str:
    return {SUPERADMIN: "主管理员", OWNER: "老板", EMPLOYEE: "员工"}.get(role, role)


def _management_page(
    db: Session,
    actor: Account,
    activation_code: str = "",
    notice: str = "",
) -> HTMLResponse:
    roles = [EMPLOYEE]
    if actor.role == SUPERADMIN:
        roles.insert(0, OWNER)
    accounts = (
        db.query(Account)
        .filter(Account.role.in_(roles))
        .order_by(Account.role.desc(), Account.created_at.desc(), Account.id.desc())
        .all()
    )
    one_time_code = ""
    if activation_code:
        one_time_code = f"""
        <section class="card" style="border-color:#f59e0b;background:#fffbeb;">
          <h2 style="margin-top:0;">请立即交给本人</h2>
          <p>一次性激活码：<strong>{html.escape(activation_code)}</strong></p>
          <p class="muted">此激活码只在本页显示一次；管理员 30 分钟、员工 24 小时有效。</p>
        </section>"""
    create_owner_form = ""
    if actor.role == SUPERADMIN:
        create_owner_form = """
        <section class="card"><h2>新增老板</h2>
          <form method="post" action="/admin/accounts/owners" class="form-grid">
            <label>姓名<input name="display_name" required maxlength="100"></label>
            <label>账号<input name="username" required maxlength="80"></label>
            <div class="actions"><button class="btn" type="submit">创建并生成激活码</button></div>
          </form>
        </section>"""
    rows = []
    for account in accounts:
        status = "启用" if account.is_active else "停用"
        binding = "已绑定" if account.wechat_openid else "未绑定"
        toggle_action = "disable" if account.is_active else "enable"
        toggle_label = "停用" if account.is_active else "启用"
        rows.append(f"""<tr>
          <td>{html.escape(account.display_name)}</td>
          <td>{html.escape(account.username.upper())}</td>
          <td>{_role_label(account.role)}</td><td>{status}</td><td>{binding}</td>
          <td><div class="actions">
            <form method="post" action="/admin/accounts/{account.id}/{toggle_action}" data-confirm="true"><button class="btn secondary" type="submit">{toggle_label}</button></form>
            <form method="post" action="/admin/accounts/{account.id}/unbind-wechat" data-confirm="true"><button class="btn secondary" type="submit">解绑微信</button></form>
            <form method="post" action="/admin/accounts/{account.id}/regenerate-activation" data-confirm="true"><button class="btn secondary" type="submit">重置激活码</button></form>
          </div></td>
        </tr>""")
    table_rows = "".join(rows) or '<tr><td colspan="6" class="muted">暂无可管理账号</td></tr>'
    body = f"""
    <div class="top"><div><h1>账号管理</h1><p class="muted">主管理员管理老板和员工；老板只能管理员工。</p></div></div>
    {one_time_code}{create_owner_form}
    <section class="card"><h2>新增员工</h2>
      <form method="post" action="/admin/accounts/employees" class="form-grid">
        <label>员工姓名<input name="display_name" required maxlength="100"></label>
        <label>工号<input name="username" required maxlength="80" placeholder="例如 TNS008"></label>
        <div class="actions"><button class="btn" type="submit">创建并生成激活码</button></div>
      </form>
    </section>
    <section class="card"><h2>账号列表</h2><table>
      <thead><tr><th>姓名</th><th>账号</th><th>角色</th><th>状态</th><th>微信</th><th>操作</th></tr></thead>
      <tbody>{table_rows}</tbody></table></section>"""
    return page("账号管理", body, notice=notice)


@router.get("/admin/accounts", response_class=HTMLResponse)
def account_management(
    db: Session = Depends(get_db),
    actor: Account = Depends(require_pc_admin_account),
) -> HTMLResponse:
    return _management_page(db, actor)


def _create_account_response(
    db: Session,
    actor: Account,
    username: str,
    display_name: str,
    role: str,
) -> HTMLResponse:
    try:
        account, activation_code = create_managed_account(
            db,
            actor=actor,
            username=username,
            display_name=display_name,
            role=role,
            commit=False,
        )
    except ValueError as exc:
        if "无权" in str(exc):
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        response = _management_page(db, actor, notice=str(exc))
        response.status_code = 400
        return response
    record_operation_log(
        db, "account_create", "account", account.id, after_data=_audit_data(account)
    )
    db.commit()
    return _management_page(db, actor, activation_code=activation_code)


@router.post("/admin/accounts/owners", response_class=HTMLResponse)
def owner_create(
    username: str = Form(...),
    display_name: str = Form(...),
    db: Session = Depends(get_db),
    actor: Account = Depends(require_pc_admin_account),
) -> HTMLResponse:
    return _create_account_response(db, actor, username, display_name, OWNER)


@router.post("/admin/accounts/employees", response_class=HTMLResponse)
def employee_create(
    username: str = Form(...),
    display_name: str = Form(...),
    db: Session = Depends(get_db),
    actor: Account = Depends(require_pc_admin_account),
) -> HTMLResponse:
    return _create_account_response(db, actor, username, display_name, EMPLOYEE)


def _redirect() -> RedirectResponse:
    return RedirectResponse("/admin/accounts", status_code=303)


def _mutate(
    db: Session,
    actor: Account,
    account_id: int,
    action: str,
) -> Response:
    account = managed_account(db, actor, account_id)
    activation_code = ""
    try:
        if action == "disable":
            disable_account(db, account, commit=False)
        elif action == "enable":
            enable_account(db, account, commit=False)
        elif action == "unbind-wechat":
            activation_code = unbind_wechat(db, account, commit=False)
        elif action == "regenerate-activation":
            activation_code = regenerate_activation(db, account, commit=False)
        else:
            raise HTTPException(status_code=404, detail="操作不存在")
    except ValueError as exc:
        response = _management_page(db, actor, notice=str(exc))
        response.status_code = 400
        return response
    record_operation_log(
        db, f"account_{action.replace('-', '_')}", "account", account.id,
        after_data=_audit_data(account),
    )
    db.commit()
    if activation_code:
        return _management_page(db, actor, activation_code=activation_code)
    return _redirect()


@router.post("/admin/accounts/{account_id}/{action}")
def account_action(
    account_id: int,
    action: str,
    db: Session = Depends(get_db),
    actor: Account = Depends(require_pc_admin_account),
) -> Response:
    return _mutate(db, actor, account_id, action)


@router.get("/admin/employees")
def employee_compatibility_page(
    actor: Account = Depends(require_pc_admin_account),
) -> RedirectResponse:
    return _redirect()


@router.post("/admin/employees", response_class=HTMLResponse)
def employee_compatibility_create(
    username: str = Form(...), display_name: str = Form(...),
    db: Session = Depends(get_db), actor: Account = Depends(require_pc_admin_account),
) -> HTMLResponse:
    return _create_account_response(db, actor, username, display_name, EMPLOYEE)


@router.post("/admin/employees/{account_id}/{action}")
def employee_compatibility_action(
    account_id: int, action: str, db: Session = Depends(get_db),
    actor: Account = Depends(require_pc_admin_account),
) -> Response:
    target = db.get(Account, account_id)
    if target is None or target.role != EMPLOYEE:
        raise HTTPException(status_code=404, detail="员工账号不存在")
    return _mutate(db, actor, account_id, action)
