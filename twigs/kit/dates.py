"""Shared date branches and 'how long ago' for mail and chats."""
import datetime
import time


def when_group(stamp, now=None):
    """Which group a thread whose newest message came at `stamp` is in, by date."""
    t, now = time.localtime(stamp), time.localtime(now or time.time())
    day = lambda x: time.mktime((x.tm_year, x.tm_mon, x.tm_mday, 0, 0, 0, 0, 0, -1))
    today = day(now)
    since = today - day(t)
    if since <= 0:
        return 'Today'
    if since <= 86400 + 3600:                   # (a summer-time change makes a day 23 or 25 hours)
        return 'Yesterday'
    monday = today - now.tm_wday * 86400
    if stamp >= monday:
        return 'This week'
    if stamp >= monday - 7 * 86400:
        return 'Last week'
    if (t.tm_year, t.tm_mon) == (now.tm_year, now.tm_mon):
        return 'Earlier this month'
    if t.tm_year == now.tm_year:
        return time.strftime('%B', t)
    return str(t.tm_year)


def ago(t, now):
    """How long ago `t` was (epoch seconds), short: 'now', '4m', '2h', then 'yesterday', a weekday
    within the week, else '3 Oct'."""
    age = now - t
    if age < 60:
        return 'now'
    if age < 3600:
        return f'{int(age // 60)}m'
    if age < 86400:
        return f'{int(age // 3600)}h'
    days = (datetime.date.fromtimestamp(now) - datetime.date.fromtimestamp(t)).days
    if days <= 1:
        return 'yesterday'
    then = datetime.date.fromtimestamp(t)
    return f'{then:%a}' if days < 7 else f'{then.day} {then:%b}'


def ago_changes(t, now):
    """When ago(t) next reads differently: the next minute, hour, or midnight it turns over."""
    age = now - t
    if age < 3600:
        return t + (int(max(age, 0) // 60) + 1) * 60
    if age < 86400:
        return t + (int(age // 3600) + 1) * 3600
    tomorrow = datetime.date.fromtimestamp(now) + datetime.timedelta(days=1)
    return time.mktime(tomorrow.timetuple())


def since(t, now):
    """ago(), as words to end a phrase: 'just now', '4m ago', '2h ago', 'yesterday', 'Tue', '3 Oct'."""
    text = ago(t, now)
    return 'just now' if text == 'now' else text + ' ago' if text[-1] in 'mh' and text[:-1].isdigit() else text
