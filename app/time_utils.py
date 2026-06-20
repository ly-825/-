from datetime import datetime
from zoneinfo import ZoneInfo


CHINA_TZ = ZoneInfo("Asia/Shanghai")


def china_now() -> datetime:
    return datetime.now(CHINA_TZ).replace(tzinfo=None)


def china_today():
    return china_now().date()
