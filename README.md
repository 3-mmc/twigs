# twigs

Small trees you move across. Your files, mail, chats, calendar and tasks, each as a tree on one
canvas: a branch opens beside the one you're on, what you've chosen stays lit, and you go by
arrows, typing to filter. Each app is a **twig**. Use one, some, or all of them side by side.

```
git clone https://github.com/3-mmc/twigs && cd twigs
uv tool install .                  # installs the Python package and console commands
twigs doctor                       # optional dependencies and platform-specific installation help
twigs apps                         # which twigs there are, and what each still needs
twigs                              # a new task desk; mail, chats and calendars as they're set up
```

Python 3.11 or newer. Files, calendars and tasks use only the standard library; mail and chats
have independent optional backends. The distribution name is **twigs-cli** (not the unrelated
PyPI package `twigs`). See [installation and packaging](docs/install.md), including how to make
the system Notmuch binding available in a virtual environment. The package is not yet on PyPI.
Source checkouts can still use `./twigs-launcher install` for the symlink workflow.

| twig | what | needs | also called |
| --- | --- | --- | --- |
| `files` | folders and files, pictures, PDFs and videos previewed in the pane; a **Files** section in `twigs` (Enter or ⌃O opens a file); `cdi` in your shell goes where you choose, ⌃T puts a path at the cursor | — (previews: pdftoppm, ffmpegthumbnailer, ImageMagick, chafa outside kitty) | |
| `msg` | mail (folders, threads, who replied to whom, ⌃Z snooze, send later) and chats (WhatsApp, Signal, Instagram) | mail: isync, notmuch, msmtp, pass, Notmuch Python bindings; chats: [nchat](https://github.com/3-mmc/nchat) for chats | `msgcanvas` |
| `cal` | calendars from .ics feeds and files, reminders, eras, and what-ifs that fork a day | — | `calcanvas` |
| `tasks` | [tuido](https://github.com/3-mmc/TuiDo)'s desk: paths, library, habits, projects; due days, timers | bundled model; no separate TuiDo installation | `tuido --canvas` |

**Settings**, the last section of `twigs` (or `twigs settings` alone), changes what twigs keeps in
`~/.config/twigs` without editing it: which twigs show (Enter; from the next start), Thunderbird's
mark-read and overlap choices, which of its accounts show (Enter), and each account's name in twigs (⌃E). Open sections take a change up at once.

In TuiDo’s Today list (and earlier pages), **Alt+↑ / Alt+↓** moves the selected entry.
The order is saved; **Ctrl+Z** undoes a move.

How each one works: [docs/msg.md](docs/msg.md), [docs/cal.md](docs/cal.md),
[docs/core.md](docs/core.md) (the canvas, `files`, and writing a provider). For cdi:
`eval "$(twigs files --init bash)"` in `~/.bashrc` (zsh and fish likewise).

## What every twig shares

The canvas (`twigs/core.py`) sleeps until something's due and draws only the lines that change,
so these cost nothing while nothing happens:

- **Beacons**: what's new and calls for you (unread mail, a reminder gone off, a task gone late, a
  timer that's rung) is white on red, blinking fast at first and slower as it ages, then lit.
- **Countdowns that stay true**: `in 3h`, `4m ago`, `⏱ 23m` turn over the moment they change.
- **Glow**: what's changed lately (a file you just saved) is tinted, fading over a week.
- **Later**: `2h`, `tonight`, `tomorrow 9`, `mon`, `3 nov` mean the same everywhere (`twigs/kit/when.py`).

`CDECK_CALM=1` keeps it all still (beacons lit, nothing blinking).

## A pinboard

**F1–F9** pins what's under the cursor into an empty slot, or goes to what's pinned there.
**Shift+F1–F9** pins it there instead; on what's already pinned there, it unpins it. **F10** (or
Ctrl+B outside tmux) clears every pin. Pins are kept for the next time, one set per kind of
canvas (`twigs`, `files`, mail), in `~/.local/state/twigs/pins/`.
On keyboards that use F-keys for system controls, use Fn too.

With pins in more than one place, the canvas becomes a pinboard. Each pin's branch is laid out
whole, side by side in slot order and wrapping into bands as wide as the screen, headed by where
it is. What you're browsing comes after the pins. A pinned folder shows what's in it, a pinned file or task
its neighbours, a message its whole text, and a chat its latest fifteen messages. Nothing is cut
to a few lines; where a pin leaves entries out, `… N more` says how many. The camera follows the
cursor and keeps as much of the board in view as fits. Pins in one branch stay open on the tree
as it is.

## Growing a twig

A twig is a package with a provider (what's in the tree: `children()`, and whichever looks it
wants, listed on `Provider` in `twigs/core.py`) and an `APP` manifest:

```python
APP = dict(name='notes', title='notes', alias=None, summary='your notes, by folder',
           cli='mytwig.cli:main',              # `twigs notes …` runs main(argv)
           hub='mytwig.cli:sections',          # what it adds to `twigs` alone: [(name, Provider)]
           needs=[('rg', 'ripgrep')])          # (a command, or 'module:name', and how to get it)
```

Twigs in this repo live in `twigs/apps/`; one in another package registers under the entry point
group `twigs.apps` (its value the module holding `APP`), and `twigs apps` finds it.

`twigs/core.py` is one file with only the standard library, so it can be copied into a program
that wants the canvas without depending on twigs: [cdeck](https://github.com/vmagierski/cdeck) does,
for its file canvas.

## Tests

```
python3 -m unittest                                              # from here: a private home, never yours
uv run --extra test pytest                                      # tasks included; mail checks skip if unavailable
```

Other mail adapters can use the [provider extension points](docs/mail-backends.md).

Optional [Thunderbird mail](docs/thunderbird.md) reuses your configured accounts: `twigs thunderbird connect` walks you through it, and can keep Thunderbird running in the background.
