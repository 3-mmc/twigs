"""twigs clock: the time in the places you keep.

    twigs clock                  open it (⌃N adds a place, ⌃W shows every clock at another time)
    twigs clock list             print them
    twigs clock add PLACE        keep a place: a city, or a zone (tokyo, "san francisco", Europe/Paris)
    twigs clock remove PLACE     forget one
"""
import os
import sys

LOOK = dict(colmax=40, glyphs='plain', ease=1 if os.environ.get('CDECK_CALM') else 0.3, peek=True)


def sections():
    """What clock adds to `twigs`: the Clock."""
    from .clock import Clock
    return [('Clock', Clock())]


def main(argv):
    from twigs import core as fc
    from . import clock
    c = clock.Clock()
    cmd = argv[0] if argv else ''
    if cmd in ('-h', '--help', 'help'):
        print(__doc__.strip())
    elif cmd == 'list' and len(argv) == 1:
        root = c.root()
        for node in root.load():
            if node.kind == 'clock-strip':
                print(c.label(node))
                continue
            print(f'{node.name:<22}{c.count(node)[0]}  {c.label(node.load()[1])}')
    elif cmd == 'add' and len(argv) > 1:
        got = clock.resolve(' '.join(argv[1:]))
        if got is None:
            sys.exit(f"twigs clock: no place called {' '.join(argv[1:])!r}: try its nearest big city, or a zone (Europe/Paris)")
        c.places.append(dict(zone=got[0], name=got[1]))
        c.save()
        print(f'{got[1]}: {got[0]}')
    elif cmd == 'remove' and len(argv) > 1:
        want = ' '.join(argv[1:]).casefold()
        hit = next((p for p in c.places if want in (p['name'].casefold(), p['zone'].casefold())), None)
        if hit is None:
            sys.exit(f"twigs clock: no {' '.join(argv[1:])!r} kept (kept: {', '.join(p['name'] for p in c.places) or 'none'})")
        c.places.remove(hit)
        c.save()
        print(f"{hit['name']} removed")
    elif not argv:
        fc.pick('', provider=c, **LOOK)
    else:
        sys.exit(__doc__.strip())
