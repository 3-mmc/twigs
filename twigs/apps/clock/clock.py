"""The world clock on a canvas: the places you keep, each with its time beside it, turning over
on the minute (the canvas sleeps in between).

    Clock
      Here            14:32        ☀ by day, ☾ by night; quiet while they're asleep there
      New York        08:32
      Tokyo           22:32
        Fri 13 Nov                 (opened: its date, its zone, how far it is from you)
        UTC+9 · JST
        8h ahead of you

    ⌃N  add a place (a city, or a zone: 'tokyo', 'san francisco', 'Europe/Paris', 'utc')
    ⌃E  rename      ⌃D  remove      ⌥↑ ⌥↓  move it
    ⌃W  when: every clock at another moment ('15:00', 'tomorrow 9', 'fri 18:30'). Asked on a
        place, the time is that place's: what '9:00' in Tokyo is here. Nothing: now again.

The places are kept in ~/.config/twigs/clocks.json. Zones are the system's (zoneinfo)."""
import datetime as dt
import json
import os
import zoneinfo

from twigs import core as fc
from twigs.kit import when

ROOT = '@clock'
PLACES = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'twigs', 'clocks.json')
# Cities people say that the zone database files under another: the name stays as it was said.
ALSO = {'san francisco': 'America/Los_Angeles', 'seattle': 'America/Los_Angeles', 'la': 'America/Los_Angeles',
        'boston': 'America/New_York', 'washington': 'America/New_York', 'nyc': 'America/New_York',
        'miami': 'America/New_York', 'atlanta': 'America/New_York', 'montreal': 'America/Toronto',
        'houston': 'America/Chicago', 'dallas': 'America/Chicago', 'austin': 'America/Chicago',
        'beijing': 'Asia/Shanghai', 'shenzhen': 'Asia/Shanghai', 'mumbai': 'Asia/Kolkata', 'delhi': 'Asia/Kolkata',
        'bangalore': 'Asia/Kolkata', 'bengaluru': 'Asia/Kolkata', 'osaka': 'Asia/Tokyo', 'hanoi': 'Asia/Bangkok',
        'abu dhabi': 'Asia/Dubai', 'tel aviv': 'Asia/Jerusalem', 'cape town': 'Africa/Johannesburg',
        'munich': 'Europe/Berlin', 'hamburg': 'Europe/Berlin', 'frankfurt': 'Europe/Berlin',
        'geneva': 'Europe/Zurich', 'barcelona': 'Europe/Madrid', 'milan': 'Europe/Rome',
        'edinburgh': 'Europe/London', 'manchester': 'Europe/London', 'st petersburg': 'Europe/Moscow',
        'rio': 'America/Sao_Paulo', 'rio de janeiro': 'America/Sao_Paulo', 'brasilia': 'America/Sao_Paulo',
        'wellington': 'Pacific/Auckland', 'canberra': 'Australia/Sydney', 'gmt': 'Etc/GMT', 'utc': 'UTC'}
WAKING = (7, 22)        # the hours a person there is likely up: outside them, their clock is quiet
DAYLIGHT = (6, 18)


def zones():
    return zoneinfo.available_timezones()


def city(zone):
    """A zone's own name for its place: 'America/Argentina/Buenos_Aires' → 'Buenos Aires'."""
    return zone.split('/')[-1].replace('_', ' ')


def resolve(text):
    """(zone, name) for a place as someone would type it, or None: a zone's name whole, a city the
    zones know ('tokyo', 'new york', 'buenos aires'), one they file elsewhere (ALSO), or the
    start or a part of one, the shortest first."""
    said = ' '.join(text.replace('_', ' ').split())
    want = said.casefold()
    if not want:
        return None
    every = sorted(zones())
    for z in every:                     # 'Europe/Paris', as it's written
        if z.casefold() == text.strip().casefold():
            return z, city(z)
    if want in ALSO:
        return ALSO[want], said.title() if said.islower() else said
    named = [z for z in every if '/' in z and not z.startswith(('Etc/', 'posix/', 'right/'))]
    for fits in (lambda c: c == want, lambda c: c.startswith(want), lambda c: want in c):
        hits = sorted((z for z in named if fits(city(z).casefold())), key=lambda z: (len(city(z)), z))
        if hits:
            return hits[0], city(hits[0])
    return None


def apart(there, here):
    """How far a place's clock is from yours: '8h ahead of you', '5h30 behind you', 'the same time as you'."""
    minutes = round((there.utcoffset() - here.utcoffset()).total_seconds() / 60)
    if not minutes:
        return 'the same time as you'
    h, m = divmod(abs(minutes), 60)
    return (f'{h}h{m:02}' if m else f'{h}h') + (' ahead of you' if minutes > 0 else ' behind you')


def offset(t):
    """'UTC+9 · JST', 'UTC−3:30', 'UTC'."""
    minutes = round(t.utcoffset().total_seconds() / 60)
    h, m = divmod(abs(minutes), 60)
    text = 'UTC' + (('+' if minutes > 0 else '−') + str(h) + (f':{m:02}' if m else '') if minutes else '')
    name = t.tzname() or ''
    return text + (f' · {name}' if name and not name.startswith(('+', '-', 'UTC')) else '')


class Clock(fc.Provider):
    """Here, then the places kept, each a branch onto its date, zone and distance from you."""
    pane = False
    settled = True          # nothing to load: the opening screen doesn't wait on it

    def __init__(self, path=PLACES, now=None, here=None):
        self.path = path
        self.fixed, self.here = now, here       # for tests: the moment it is, and the zone you're in
        self.at = None                          # ⌃W: the moment the clocks show instead of now
        self.shown = False                      # opened yet: until then nothing needs to tick
        try:
            with open(path) as f:
                self.places = [dict(zone=p['zone'], name=p.get('name') or city(p['zone']))
                               for p in json.load(f) if p.get('zone') in zones()]
        except (OSError, ValueError, TypeError, AttributeError):
            self.places = []

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temp = self.path + '.tmp'
        with open(temp, 'w') as f:
            json.dump(self.places, f, indent=1, ensure_ascii=False)
        os.replace(temp, self.path)

    # -- the time
    def now(self):
        """The moment the clocks show, in your zone: now, or the one asked for (⌃W)."""
        if self.at is not None:
            return self.at
        t = self.fixed or dt.datetime.now(dt.timezone.utc)
        return t.astimezone(self.here) if self.here else t.astimezone()

    def there(self, node):
        """The moment, on `node`'s clock (a place's, or a line under one); here's for anything else."""
        while node is not None and node.kind not in ('place', 'here'):
            node = node.parent
        now = self.now()
        return now.astimezone(zoneinfo.ZoneInfo(node.data['zone'])) if node is not None and node.kind == 'place' else now

    def next_change(self):
        """The next minute, while the clocks run and have been looked at."""
        if not self.shown or self.at is not None or self.fixed:
            return None
        t = dt.datetime.now().timestamp()
        return t - t % 60 + 60

    def loading(self):
        return 'the clocks', True

    def reach(self, node):
        return True

    # -- the tree
    def root(self):
        return fc.Node(ROOT, 'Clock', True, source=self, kind='clock')

    def children(self, node):
        self.shown = True
        if node.kind == 'clock':
            return [fc.Node(f'{ROOT}/here', 'Here', True, node, 'here')] + \
                [fc.Node(f'{ROOT}/{n}:{p["zone"]}', p['name'], True, node, 'place', data=p)
                 for n, p in enumerate(self.places)]
        if node.kind in ('place', 'here'):
            lines = ['date', 'zone'] + (['apart'] if node.kind == 'place' else [])
            return [fc.Node(f'{node.path}/{what}', what, False, node, 'clock-line', data=what) for what in lines]
        return []

    def label(self, node):
        if node.kind == 'clock' and self.at is not None:
            return f'Clock · at {when.say(self.at, self.fixed_or_now())}'
        if node.kind != 'clock-line':
            return node.name
        t = self.there(node)
        if node.data == 'date':
            return f'{t:%a} {t.day} {t:%b}'
        if node.data == 'zone':
            zone = node.parent.data['zone'] if node.parent.kind == 'place' else self.home()
            return offset(t) + (f' · {zone}' if zone and zone != 'UTC' else '')
        return apart(t, self.now())

    def home(self):
        """Your own zone's name, where the system says it ('Europe/Berlin'); '' where it doesn't."""
        if self.here is not None:
            return getattr(self.here, 'key', '')
        try:
            return os.path.realpath('/etc/localtime').split('/zoneinfo/', 1)[1]
        except (OSError, IndexError):
            return ''

    def fixed_or_now(self):
        t = self.fixed or dt.datetime.now(dt.timezone.utc)
        return t.astimezone(self.here) if self.here else t.astimezone()

    def count(self, node):
        """The time there, and its weekday when that's not yours; quiet while they're asleep."""
        if node.kind not in ('place', 'here'):
            return None
        t = self.there(node)       # its weekday too, when that isn't today's here (another moment's: any)
        text = f'{t:%H:%M}' + (f' {t:%a}' if t.date() != self.fixed_or_now().date() else '')
        up = WAKING[0] <= t.hour < WAKING[1]
        return text, 'file' if up else 'stub', node.kind == 'here'

    def mark(self, node):
        if node.kind not in ('place', 'here'):
            return None
        return ('state_doing', '☀', '*') if DAYLIGHT[0] <= self.there(node).hour < DAYLIGHT[1] else ('stub', '☾', '.')

    def mark_before(self, node):
        return True

    def colour(self, node):
        return ('stub', False) if node.kind == 'clock-line' else None

    def copy_text(self, node):
        if node.kind in ('place', 'here'):
            return f'{node.name} {self.count(node)[0]}'
        return self.label(node)

    def choose(self, node):
        return None if node.is_dir else fc.STAY

    def bar(self, node, plain):
        segs, tail = super().bar(node, plain)
        if self.at is not None:
            tail = list(tail) + [('not now: ⌃W, nothing, for now again', 'bg', 'filter', False)]
        return segs, tail

    # -- doing
    def actions(self, node):
        out = [('\x0e', 'new', '⌃N add a place'), ('\x17', 'when', '⌃W now again' if self.at is not None else '⌃W at…')]
        at = node if node.kind == 'place' else node.parent if node.kind == 'clock-line' and node.parent.kind == 'place' else None
        if at is not None:
            out += [('\x05', 'rename', '⌃E rename'), ('\x04', 'remove', '⌃D remove')]
        if node.kind == 'place':
            out += [('alt-up', 'up', '⌥↑↓ move'), ('alt-down', 'down', '')]
        return out

    def act(self, node, name, canvas):
        place = node if node.kind == 'place' else node.parent if node.kind == 'clock-line' and node.parent.kind == 'place' else None
        index = self.places.index(place.data) if place is not None and place.data in self.places else None

        def changed(to=None):
            self.save()
            self.invalidate(ROOT, deep=True)
            canvas.refresh()
            if to is not None and canvas.go(list(canvas.chain(self.top(node))) + [f'{ROOT}/{to}:{self.places[to]["zone"]}']):
                self.pulse(canvas.focus.path)

        if name == 'new':
            def add(text):
                if not text.strip():
                    return None
                got = resolve(text)
                if got is None:
                    canvas.status('clock', f"no place called {text.strip()!r}: try its nearest big city, or a zone (Europe/Paris)", 'failed')
                    return None
                zone, called = got
                self.places.append(dict(zone=zone, name=called))
                changed(len(self.places) - 1)
                canvas.status('clock', f'{called}: {zone}', 'ok')
                return None
            canvas.ask('add a place (a city, or a zone)', add)
        elif name == 'when':
            if self.at is not None:
                self.at = None
                self.invalidate(ROOT)
                canvas.status('clock', 'now again', 'note')
                return None
            zone = zoneinfo.ZoneInfo(place.data['zone']) if place is not None else None
            where = f' in {place.name}' if place is not None else ''

            def ask(text):
                if not text.strip():
                    return None
                base = self.fixed_or_now()
                got = when.parse(text, base.astimezone(zone) if zone else base)
                if got is None:
                    canvas.status('clock', 'when is that? say 15:00, tomorrow 9 or fri 18:30 (a time still to come)', 'failed')
                    return None
                self.at = got.astimezone(base.tzinfo)
                self.invalidate(ROOT)
                canvas.status('clock', f'every clock at {text.strip()}{where}', 'note')
                return None
            canvas.ask(f'what time{where} (15:00, tomorrow 9, fri 18:30)', ask)
        elif name == 'rename' and index is not None:
            def rename(text):
                if text.strip():
                    self.places[index]['name'] = text.strip()
                    changed(index)
                return None
            canvas.ask('rename', rename, text=place.name)
        elif name == 'remove' and index is not None:
            gone = self.places.pop(index)
            changed()
            canvas.status('clock', gone['name'], 'gone')
        elif name in ('up', 'down') and index is not None:
            to = index + (-1 if name == 'up' else 1)
            if not 0 <= to < len(self.places):
                canvas.status('clock', 'first already' if name == 'up' else 'last already', 'note')
                return None
            self.places[index], self.places[to] = self.places[to], self.places[index]
            changed(to)
        return None

    @staticmethod
    def top(node):
        while node.kind != 'clock':
            node = node.parent
        return node
