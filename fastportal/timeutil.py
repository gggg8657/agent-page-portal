"""시각 규칙 — 저장은 UTC, 보여 주기·달력은 한국 시간(KST, Asia/Seoul, UTC+9).

- 저장: 지금까지처럼 타임존 표시 없는 ISO(초 단위) = UTC. 예전 값도 모두 UTC 이므로 옮기거나 고치지 않는다.
  서버 TZ 환경변수와 상관없이 UTC 가 되게 now_iso() 를 쓴다(TZ=Asia/Seoul 을 전역으로 켜면 새 값만 KST 가 되어 섞임 — 금지).
- 읽기: 타임존 표시 없는 값은 UTC 로 본다. 'Z'·'+00:00' 등 표시가 있으면 그대로, 숫자는 epoch 초(1e12 넘으면 ms).
- 달력: '오늘'·이번 주(ISO)·이번 달·'최근 N일'·하루 한도는 KST 날짜로 계산한다. 저장된 UTC 값과 견줄 때는
  kst_day_start_utc() 로 'KST 자정'을 UTC ISO 로 바꿔 문자열 비교한다.
"""
import datetime

try:
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
except Exception:   # tzdata 가 없는 환경 — 한국은 일광절약시간이 없어 +09:00 고정과 같다
    KST = datetime.timezone(datetime.timedelta(hours=9), "KST")
UTC = datetime.timezone.utc


def utcnow():
    """지금(UTC, 타임존 표시 없는 datetime) — 저장용"""
    return datetime.datetime.now(UTC).replace(tzinfo=None)


def now_iso():
    """저장용 지금 시각 문자열 'YYYY-MM-DDTHH:MM:SS'(UTC, 타임존 표시 없음 — 예전 값과 같은 형식)"""
    return utcnow().isoformat(timespec="seconds")


def now_kst(now=None):
    """지금(KST, aware). now: 시험용 고정 시각(aware 또는 naive=UTC)"""
    return to_kst(now) if now is not None else datetime.datetime.now(KST)


def today_kst(now=None):
    return now_kst(now).date()


def parse(v):
    """ISO 문자열·epoch 초/ms·datetime → aware UTC datetime. 타임존 표시 없으면 UTC. 못 읽으면 None"""
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, datetime.datetime):
        d = v
    elif isinstance(v, datetime.date):
        d = datetime.datetime.combine(v, datetime.time())
    elif isinstance(v, (int, float)):
        d = datetime.datetime.fromtimestamp(v / 1000 if abs(v) > 1e12 else v, UTC)
    else:
        s = str(v).strip()
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        try:
            d = datetime.datetime.fromisoformat(s)
        except ValueError:
            return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def to_kst(v):
    d = parse(v)
    return d.astimezone(KST) if d else None


def fmt_kst(v, fmt="%Y-%m-%d %H:%M", label=False):
    """보여 줄 KST 글자. label=True 면 끝에 ' KST'. 못 읽으면 원래 값 그대로(빈 값은 '')"""
    d = to_kst(v)
    if d is None:
        return "" if v in (None, "") else str(v)
    return d.strftime(fmt) + (" KST" if label else "")


def kst_date(v):
    """저장된 시각(naive=UTC) → KST 날짜(date). 못 읽으면 None"""
    d = to_kst(v)
    return d.date() if d else None


def kst_day_start_utc(days_ago=0, now=None):
    """KST 달력으로 days_ago 일 전 0시를 저장 형식(UTC, 타임존 표시 없는 ISO)으로 — 'ts >= ?' 비교용"""
    day = today_kst(now) - datetime.timedelta(days=days_ago)
    start = datetime.datetime.combine(day, datetime.time(), KST)
    return start.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def kst_day_start_epoch(day):
    """KST 날짜 0시의 epoch 초(jobs.started 처럼 epoch 로 저장된 값과 비교)"""
    return datetime.datetime.combine(day, datetime.time(), KST).timestamp()


def iso_week(now=None):
    """KST 기준 ISO 주 'YYYY-Www'"""
    y, w, _ = today_kst(now).isocalendar()
    return f"{y}-W{w:02d}"
