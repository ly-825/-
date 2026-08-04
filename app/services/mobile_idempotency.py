import hashlib
import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import MobileRequestRecord


def _request_id(value: str | None) -> str:
    request_id = (value or "").strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="client_request_id 不能为空")
    if len(request_id) > 100:
        raise HTTPException(status_code=422, detail="client_request_id 不能超过100个字符")
    return request_id


def payload_fingerprint(payload: dict[str, object]) -> str:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def replayed_mobile_response(
    db: Session,
    operation_type: str,
    client_request_id: str | None,
    payload: dict[str, object],
) -> dict | None:
    request_id = _request_id(client_request_id)
    existing = (
        db.query(MobileRequestRecord)
        .filter(
            MobileRequestRecord.operation_type == operation_type,
            MobileRequestRecord.client_request_id == request_id,
        )
        .first()
    )
    if existing is None:
        return None
    if existing.request_fingerprint != payload_fingerprint(payload):
        raise HTTPException(
            status_code=409,
            detail="同一请求编号不能提交不同内容，请刷新页面后重试",
        )
    return dict(existing.response_json)


def remember_mobile_response(
    db: Session,
    operation_type: str,
    client_request_id: str | None,
    payload: dict[str, object],
    response: dict,
) -> MobileRequestRecord:
    record = MobileRequestRecord(
        operation_type=operation_type,
        client_request_id=_request_id(client_request_id),
        request_fingerprint=payload_fingerprint(payload),
        response_json=response,
    )
    db.add(record)
    return record
