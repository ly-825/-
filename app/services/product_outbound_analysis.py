from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import InventoryTransactionRecord, MaterialInventory
from app.time_utils import china_now


OUTBOUND_PURPOSES = (
    ("sales", "销售/发货"),
    ("internal", "生产领用"),
    ("sample", "样品"),
    ("repair", "返修"),
    ("scrap", "报废"),
    ("other", "其他"),
)

PURPOSE_LABELS = dict(OUTBOUND_PURPOSES)
SALES_PURPOSES = {"", "sales", None}


def normalize_outbound_purpose(value: str | None, default: str = "sales") -> str | None:
    text = (value or "").strip()
    allowed = {key for key, _ in OUTBOUND_PURPOSES}
    if text in allowed:
        return text
    return default if default in allowed else None


def outbound_purpose_label(value: str | None) -> str:
    if value in (None, ""):
        return "销售/发货"
    return PURPOSE_LABELS.get(value, str(value))


def product_outbound_period_range(period: str, start_date: str = "", end_date: str = "") -> tuple[datetime, datetime, str]:
    start_text = start_date.strip()
    end_text = end_date.strip()
    if start_text and end_text:
        start = datetime.strptime(start_text, "%Y-%m-%d")
        end = datetime.strptime(end_text, "%Y-%m-%d") + timedelta(days=1)
        return start, end, f"{start_text} 至 {end_text}"
    now = china_now()
    today_start = datetime(now.year, now.month, now.day)
    if period == "today":
        return today_start, today_start + timedelta(days=1), "今天"
    if period == "week":
        start = today_start - timedelta(days=today_start.weekday())
        return start, now + timedelta(days=1), "本周"
    if period == "month":
        return datetime(now.year, now.month, 1), now + timedelta(days=1), "本月"
    if period == "quarter":
        quarter_month = ((now.month - 1) // 3) * 3 + 1
        return datetime(now.year, quarter_month, 1), now + timedelta(days=1), "本季度"
    if period == "year":
        return datetime(now.year, 1, 1), now + timedelta(days=1), "本年"
    if period == "recent_30":
        return today_start - timedelta(days=29), now + timedelta(days=1), "近30天"
    if period == "recent_90":
        return today_start - timedelta(days=89), now + timedelta(days=1), "近90天"
    return today_start - timedelta(days=364), now + timedelta(days=1), "近一年"


def _month_span(start: datetime, end: datetime) -> int:
    end_date = end - timedelta(days=1)
    return max(1, (end_date.year - start.year) * 12 + end_date.month - start.month + 1)


def _fmt_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _record_is_sales(record: InventoryTransactionRecord) -> bool:
    return record.outbound_purpose in SALES_PURPOSES


def analyze_product_outbound(
    db: Session,
    product_code: str = "",
    period: str = "recent_365",
    start_date: str = "",
    end_date: str = "",
    customer: str = "",
    purpose: str = "",
) -> dict:
    start, end, range_label = product_outbound_period_range(period, start_date, end_date)
    records = (
        db.query(InventoryTransactionRecord)
        .filter(
            InventoryTransactionRecord.transaction_type == "out",
            InventoryTransactionRecord.reversed_transaction_id.is_(None),
            InventoryTransactionRecord.created_at >= start,
            InventoryTransactionRecord.created_at < end,
        )
        .order_by(InventoryTransactionRecord.created_at.desc(), InventoryTransactionRecord.id.desc())
        .all()
    )
    inventory_ids = [record.inventory_id for record in records]
    inventory_map = {
        item.id: item
        for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()
    } if inventory_ids else {}

    product_filter = product_code.strip()
    customer_filter = customer.strip()
    purpose_filter = purpose.strip()
    detail_rows = []
    monthly: dict[str, dict] = {}
    customers = set()
    total_quantity = 0
    sales_quantity = 0

    for record in records:
        item = inventory_map.get(record.inventory_id)
        if not item or item.inventory_type != "product":
            continue
        code = item.material_code or item.source_product_code or "未编号"
        if product_filter and product_filter != code:
            continue
        customer_name = record.customer_name or ""
        if customer_filter and customer_filter not in customer_name:
            continue
        if purpose_filter == "sales":
            if not _record_is_sales(record):
                continue
        elif purpose_filter and record.outbound_purpose != purpose_filter:
            continue

        purpose_key = record.outbound_purpose or "sales"
        purpose_label = outbound_purpose_label(record.outbound_purpose)
        month = record.created_at.strftime("%Y-%m") if record.created_at else "-"
        group = monthly.setdefault(
            month,
            {"month": month, "quantity": 0, "sales_quantity": 0, "transaction_count": 0, "customers": set()},
        )
        group["quantity"] += record.quantity
        group["transaction_count"] += 1
        total_quantity += record.quantity
        if _record_is_sales(record):
            group["sales_quantity"] += record.quantity
            sales_quantity += record.quantity
        if customer_name:
            group["customers"].add(customer_name)
            customers.add(customer_name)
        detail_rows.append(
            {
                "transaction_id": record.id,
                "time": _fmt_time(record.created_at),
                "product_code": code,
                "quantity": record.quantity,
                "customer_name": customer_name or "-",
                "purpose": purpose_key,
                "purpose_label": purpose_label,
                "location": item.location or "-",
                "material": item.material or "-",
                "thickness": item.thickness,
                "operator_name": record.operator_name or "-",
                "remark": record.remark or "-",
            }
        )

    monthly_rows = []
    for row in sorted(monthly.values(), key=lambda value: value["month"]):
        monthly_rows.append(
            {
                "month": row["month"],
                "quantity": row["quantity"],
                "sales_quantity": row["sales_quantity"],
                "transaction_count": row["transaction_count"],
                "customer_count": len(row["customers"]),
            }
        )
    months = _month_span(start, end)
    monthly_avg = round(sales_quantity / months, 1) if months else 0
    recent_months = monthly_rows[-3:]
    recent_avg = round(sum(row["sales_quantity"] for row in recent_months) / len(recent_months), 1) if recent_months else 0
    base_avg = max(monthly_avg, recent_avg)
    suggested_year_quantity = int(round(base_avg * 12)) if base_avg else 0
    summary = {
        "range_label": range_label,
        "product_code": product_filter or "全部产品",
        "total_quantity": total_quantity,
        "sales_quantity": sales_quantity,
        "transaction_count": len(detail_rows),
        "customer_count": len(customers),
        "month_count": months,
        "monthly_avg": monthly_avg,
        "recent_3_month_avg": recent_avg,
        "peak_month_quantity": max((row["sales_quantity"] for row in monthly_rows), default=0),
        "suggested_year_quantity": suggested_year_quantity,
        "safety_stock_10": int(round(suggested_year_quantity * 1.1)) if suggested_year_quantity else 0,
        "safety_stock_20": int(round(suggested_year_quantity * 1.2)) if suggested_year_quantity else 0,
    }
    return {
        "summary": summary,
        "monthly_rows": monthly_rows,
        "detail_rows": detail_rows,
        "start": start,
        "end": end,
    }


def product_outbound_analysis_export_rows(db: Session, filters: dict) -> tuple[list[str], list[list[object]]]:
    result = analyze_product_outbound(
        db,
        product_code=filters.get("product_code") or "",
        period=filters.get("period") or "recent_365",
        start_date=filters.get("start_date") or "",
        end_date=filters.get("end_date") or "",
        customer=filters.get("customer") or "",
        purpose=filters.get("purpose") or "",
    )
    rows = [
        [
            row["transaction_id"],
            row["time"],
            row["product_code"],
            row["quantity"],
            row["customer_name"],
            row["purpose_label"],
            row["location"],
            row["material"],
            row["thickness"],
            row["operator_name"],
            row["remark"],
            result["summary"]["range_label"],
        ]
        for row in result["detail_rows"]
    ]
    return ["流水号", "出库时间", "产品型号", "出库数量", "客户/去向", "用途", "库位", "材质", "厚度", "操作人", "备注", "时间范围"], rows
