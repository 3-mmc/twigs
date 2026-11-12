"""The world clock: places and their times at a fixed moment, adding by name, another moment."""
import datetime as dt
import json
import re
import zoneinfo

import pytest

from twigs import core as fc
from twigs.apps.clock import clock

ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]')
BERLIN = zoneinfo.ZoneInfo('Europe/Berlin')
NOON = dt.datetime(2026, 11, 12, 13, 30, tzinfo=BERLIN)       # a Thursday, 13:30 in Berlin


@pytest.fixture
def made(tmp_path):
    path = tmp_path / 'clocks.json'
    path.write_text(json.dumps([dict(zone='America/New_York', name='Dana'), dict(zone='Asia/Tokyo'),
                                dict(zone='Not/AZone', name='gone')]))
    p = clock.Clock(str(path), now=NOON, here=BERLIN)
    return p, fc.Canvas('', fc.options(provider=p, backdrop='off', ease=1, glyphs='plain', colmax=40, peek=True)), path


def press(c, *keys):
    for k in keys:
        fc.apply(c, k, c.opts)


def screen(c, w=110, h=24):
    return [ANSI.sub('', l).rstrip() for l in c.frame(w, h, animate=False)[0]]


def test_places_are_found_as_people_say_them():
    assert clock.resolve('tokyo') == ('Asia/Tokyo', 'Tokyo')
    assert clock.resolve('New york') == ('America/New_York', 'New York')
    assert clock.resolve('san francisco') == ('America/Los_Angeles', 'San Francisco')   # filed under another city
    assert clock.resolve('europe/paris') == ('Europe/Paris', 'Paris')
    assert clock.resolve('lond') == ('Europe/London', 'London')
    assert clock.resolve('utc') == ('UTC', 'UTC')
    assert clock.resolve('nowhere at all') is None and clock.resolve('  ') is None


def test_each_place_says_its_time_and_how_far_it_is(made):
    p, c, _ = made
    here, dana, tokyo = c.root.load()                           # a zone that isn't one is left out
    assert [n.name for n in (here, dana, tokyo)] == ['Here', 'Dana', 'Tokyo']
    assert p.count(here) == ('13:30', 'file', True)
    assert p.count(dana)[0] == '07:30' and p.mark(dana)[1] == '☀'
    assert p.count(tokyo)[:2] == ('21:30', 'file') and p.mark(tokyo)[1] == '☾'
    assert [p.label(n) for n in tokyo.load()] == ['Thu 12 Nov', 'UTC+9 · JST · Asia/Tokyo', '8h ahead of you']
    assert p.label(dana.load()[2]) == '6h behind you' and p.label(here.load()[1]) == 'UTC+1 · CET · Europe/Berlin'
    assert clock.apart(NOON.astimezone(zoneinfo.ZoneInfo('Asia/Kolkata')), NOON) == '4h30 ahead of you'
    assert clock.offset(NOON.astimezone(dt.timezone.utc)) == 'UTC'
    shown = '\n'.join(screen(c))
    assert 'Dana' in shown and '07:30' in shown and '21:30' in shown
    assert p.next_change() is None                              # a fixed moment doesn't tick


def test_a_running_clock_turns_over_on_the_minute_once_looked_at(tmp_path):
    p = clock.Clock(str(tmp_path / 'none.json'))
    assert p.next_change() is None                              # not opened: nothing to redraw
    p.root().load()
    left = p.next_change() - dt.datetime.now().timestamp()
    assert 0 < left <= 60


def test_add_rename_move_and_remove_are_kept(made):
    p, c, path = made
    press(c, '\x0e', *'buenos aires', '\r')                     # ⌃N: found by name, and you're on it
    assert c.focus.name == 'Buenos Aires' and p.count(c.focus)[0] == '09:30'
    press(c, '\x0e', *'atlantis', '\r')
    assert c.statuses['clock'][1] == 'failed' and len(p.places) == 3
    press(c, '\x05', '\x15', *'Lucía', '\r')                    # ⌃E
    press(c, 'alt-up', 'alt-up', 'alt-up')                      # to the top, and no further
    assert [x['name'] for x in json.loads(path.read_text())] == ['Lucía', 'Dana', 'Tokyo']
    assert c.focus.name == 'Lucía' and c.statuses['clock'][0] == 'first already'
    press(c, '\x04')                                            # ⌃D
    assert [x['name'] for x in clock.Clock(str(path)).places] == ['Dana', 'Tokyo']
    assert {k for k, _, _ in p.actions(c.root.load()[0])} == {'\x0e', '\x17'}      # Here: nothing to rename


def test_w_shows_every_clock_at_another_moment_in_a_places_own_time(made):
    p, c, _ = made
    here, dana, tokyo = c.root.load()
    c.go(c.chain(tokyo))
    press(c, '\x17', *'tomorrow 9:00', '\r')                    # 9:00 tomorrow in Tokyo
    assert p.count(tokyo)[0] == '09:00 Fri' and p.count(here)[0] == '01:00 Fri' and p.count(dana)[0] == '19:00'
    assert p.count(here)[1] == 'stub'                           # asleep here then: quiet
    assert 'at' in c.root.label() and any('now again' in h for _, _, h in p.actions(tokyo))
    press(c, '\x17')                                            # again: now
    assert p.at is None and p.count(here)[0] == '13:30'
    press(c, '\x17', *'whenever', '\r')
    assert c.statuses['clock'][1] == 'failed' and p.at is None
