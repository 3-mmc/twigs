#!/usr/bin/env python3
"""treecanvas: a tree as one canvas you move across; the file system, unless you give it another.
(It was filecanvas: the file system is still what it shows by default.)

Every folder you open keeps its place on a tidy-tree layout (depth to the right, children
spread down, each block of children centred on its parent and shifted only as much as it
needs to keep clear of its neighbours). A camera eases after the selection and only moves
when the selection leaves a box around the middle of the screen. The path you're on is drawn
in the theme's path colour; everything else dims with its distance from it.

    python3 treecanvas.py [START]          # prints the chosen path on stdout, nothing on Esc
    cd "$(python3 treecanvas.py)"

In your shell: `cdi` goes to a folder you choose, ⌃T puts a path in at the cursor:

    eval "$(python3 /path/to/treecanvas.py --init bash)"     # or zsh; fish: --init fish | source

Keys: ↑↓ siblings · → or Tab open · ← parent (the branch stays open) · type to filter this
column · Backspace unfilters · ⌃X fold everything off the current path · Enter choose · Esc.
Settings are flags (--help), and any of them can live in ~/.config/treecanvas/config.json.

The file system is one provider of the tree (FileSystem); anything else can be another
(Provider, and Tree for nested dicts): `pick(provider=...)`.
"""
import argparse, functools, json, math, os, re, select, shutil, signal, stat, subprocess, sys, threading, time, unicodedata
from concurrent.futures import ThreadPoolExecutor

CONFIG = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'),
                      'treecanvas', 'config.json')
OLD_CONFIG = CONFIG.replace(os.sep + 'treecanvas' + os.sep, os.sep + 'filecanvas' + os.sep)   # its name before

# -- colour

# A theme names colours by role. A colour is a 256-colour number or '#rrggbb'.
#   sel_fg/sel_bg   the chosen entry         path       folders on the path you're on
#   path_line       connectors on the path   line       other connectors (brightest)
#   dir / file      entries, when LS_COLORS isn't used, or has nothing for them
#   stub            "(empty)", "↓ 12 more"   filter     the filter chip
#   hint            the key hints            bg         what dimmed colours fade into
#   rest            branches you're not on (--grey-rest)
#   ghost           the screen behind, dimmed (--backdrop)
#   home_* seg_* cwd_fg sep_fg               the path bar, as powerline-shell draws a prompt
#   repo_clean_* repo_dirty_*                the bar's git chip
#   git_dirty git_staged git_untracked git_conflict   the marks beside changed entries
#   alert_fg alert_bg                  a beacon lit (git_conflict's red and white unless a theme says)
#   glow                               what's changed lately is tinted towards (sel_bg unless a theme says)
#   person0 … person9, person_fg       chips for people (a chat's senders), and their text
#   state_done state_doing             what's finished and what's under way (a task tree's)
# `ls_colors` is the theme's default for --ls-colors; you can still change it.
THEMES = {
    'powerline': {  # powerline-shell's default theme
        'bg': '#121212', 'sel_bg': 31, 'sel_fg': 15, 'path': 254, 'path_line': 31,
        'line': 244, 'dir': 110, 'file': 250, 'stub': 242, 'filter': 148, 'hint': 242,
        'home_bg': 31, 'home_fg': 15, 'seg_bg': 237, 'seg_fg': 250, 'cwd_fg': 254, 'sep_fg': 244,
        'repo_clean_bg': 148, 'repo_clean_fg': 0, 'repo_dirty_bg': 161, 'repo_dirty_fg': 15,
        'git_dirty': 161, 'git_staged': 148, 'git_untracked': 130, 'git_conflict': 9,
        'rest': 248, 'ghost': 237, 'ls_colors': True,
        'state_done': 114, 'state_doing': 179,
        'person_fg': 15, 'person0': 24, 'person1': 23, 'person2': 58, 'person3': 96, 'person4': 130, 'person5': 61,
        'person6': 28, 'person7': 161, 'person8': 91, 'person9': 17,
    },
    'amber': {  # cdeck's
        'bg': '#121212', 'sel_bg': 214, 'sel_fg': 232, 'path': 214, 'path_line': 214,
        'line': 244, 'dir': 222, 'file': 250, 'stub': 240, 'filter': 214, 'hint': 240,
        'home_bg': 214, 'home_fg': 232, 'seg_bg': 94, 'seg_fg': 222, 'cwd_fg': 230, 'sep_fg': 172,
        'repo_clean_bg': 136, 'repo_clean_fg': 232, 'repo_dirty_bg': 166, 'repo_dirty_fg': 232,
        'git_dirty': 166, 'git_staged': 142, 'git_untracked': 137, 'git_conflict': 196,
        'rest': 246, 'ghost': 237, 'ls_colors': True,
        'state_done': 107, 'state_doing': 214,
        'person_fg': 230, 'person0': 94, 'person1': 130, 'person2': 58, 'person3': 95, 'person4': 23, 'person5': 88,
        'person6': 22, 'person7': 54, 'person8': 60, 'person9': 125,
    },
    'nord': {
        'bg': '#2e3440', 'sel_bg': '#88c0d0', 'sel_fg': '#2e3440', 'path': '#88c0d0',
        'path_line': '#81a1c1', 'line': '#616e88', 'dir': '#81a1c1', 'file': '#d8dee9',
        'stub': '#4c566a', 'filter': '#ebcb8b', 'hint': '#4c566a',
        'home_bg': '#5e81ac', 'home_fg': '#eceff4', 'seg_bg': '#3b4252', 'seg_fg': '#d8dee9',
        'cwd_fg': '#eceff4', 'sep_fg': '#4c566a',
        'repo_clean_bg': '#a3be8c', 'repo_clean_fg': '#2e3440', 'repo_dirty_bg': '#bf616a',
        'repo_dirty_fg': '#eceff4', 'git_dirty': '#d08770', 'git_staged': '#a3be8c',
        'git_untracked': '#b48ead', 'git_conflict': '#bf616a',
        'rest': '#a7adba', 'ghost': '#434c5e', 'ls_colors': True,
        'state_done': '#a3be8c', 'state_doing': '#ebcb8b',
        'person_fg': '#eceff4', 'person0': '#4c6a94', 'person1': '#3e6573', 'person2': '#56653a', 'person3': '#6a4a6a', 'person4': '#8c5040', 'person5': '#4e5c88',
        'person6': '#337744', 'person7': '#773355', 'person8': '#553377', 'person9': '#776633',
    },
    'gruvbox': {
        'bg': '#282828', 'sel_bg': '#fabd2f', 'sel_fg': '#282828', 'path': '#fabd2f',
        'path_line': '#d79921', 'line': '#7c6f64', 'dir': '#83a598', 'file': '#ebdbb2',
        'stub': '#665c54', 'filter': '#b8bb26', 'hint': '#665c54',
        'home_bg': '#458588', 'home_fg': '#ebdbb2', 'seg_bg': '#504945', 'seg_fg': '#d5c4a1',
        'cwd_fg': '#fbf1c7', 'sep_fg': '#7c6f64',
        'repo_clean_bg': '#98971a', 'repo_clean_fg': '#282828', 'repo_dirty_bg': '#cc241d',
        'repo_dirty_fg': '#ebdbb2', 'git_dirty': '#fe8019', 'git_staged': '#b8bb26',
        'git_untracked': '#d3869b', 'git_conflict': '#fb4934',
        'rest': '#a89984', 'ghost': '#504945', 'ls_colors': True,
        'state_done': '#b8bb26', 'state_doing': '#fabd2f',
        'person_fg': '#282828', 'person0': '#83a598', 'person1': '#8ec07c', 'person2': '#b8bb26', 'person3': '#d3869b', 'person4': '#fe8019', 'person5': '#fabd2f',
        'person6': '#a89984', 'person7': '#c9a0dc', 'person8': '#7fb4ca', 'person9': '#e9b4a0',
    },
    'solarized-dark': {
        'bg': '#002b36', 'sel_bg': '#268bd2', 'sel_fg': '#fdf6e3', 'path': '#93a1a1',
        'path_line': '#268bd2', 'line': '#586e75', 'dir': '#268bd2', 'file': '#839496',
        'stub': '#586e75', 'filter': '#b58900', 'hint': '#586e75',
        'home_bg': '#268bd2', 'home_fg': '#fdf6e3', 'seg_bg': '#073642', 'seg_fg': '#93a1a1',
        'cwd_fg': '#eee8d5', 'sep_fg': '#586e75',
        'repo_clean_bg': '#859900', 'repo_clean_fg': '#002b36', 'repo_dirty_bg': '#dc322f',
        'repo_dirty_fg': '#fdf6e3', 'git_dirty': '#cb4b16', 'git_staged': '#859900',
        'git_untracked': '#6c71c4', 'git_conflict': '#dc322f',
        'rest': '#839496', 'ghost': '#073642', 'ls_colors': True,
        'state_done': '#859900', 'state_doing': '#b58900',
        'person_fg': '#002b36', 'person0': '#3693d6', 'person1': '#2aa198', 'person2': '#859900', 'person3': '#8487cc', 'person4': '#df619a', 'person5': '#d77148',
        'person6': '#b58900', 'person7': '#93a1a1', 'person8': '#e5726f', 'person9': '#a0d070',
    },
}

# Separators: (hard, soft, left cap of the chosen entry, right cap). Plain needs no special font.
GLYPHS = {
    'powerline': ('', '', '', ''),
    'round': ('', '', '', ''),   # needs a Nerd Font
    'plain': ('', '/', '', ''),
}
MARKS = {  # git state -> (role, glyph, plain glyph)
    4: ('git_conflict', '✖', '!'), 3: ('git_dirty', '●', 'M'),
    2: ('git_staged', '✚', '+'), 1: ('git_untracked', '?', '?'),
}

# How bright something is, by how far it is from the choice (or the path): 1 is full colour.
STRENGTH = (1.0, .9, .8, .72, .64, .56, .5, .45, .4)

XTERM16 = [(0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0), (0, 0, 238), (205, 0, 205),
           (0, 205, 205), (229, 229, 229), (127, 127, 127), (255, 0, 0), (0, 255, 0),
           (255, 255, 0), (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255)]
LEVELS = (0, 95, 135, 175, 215, 255)


def rgb(c):
    if isinstance(c, tuple):
        return c
    if isinstance(c, str):
        c = c.lstrip('#')
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    if c < 16:
        return XTERM16[c]
    if c < 232:
        c -= 16
        return LEVELS[c // 36], LEVELS[c // 6 % 6], LEVELS[c % 6]
    return (8 + 10 * (c - 232),) * 3


@functools.lru_cache(maxsize=4096)
def nearest(t):
    """The 256-colour number closest to an RGB triple (remembered: a frame asks for the same few)."""
    def d(a):
        return sum((x - y) ** 2 for x, y in zip(a, t))
    cube = [min(range(6), key=lambda i: abs(LEVELS[i] - v)) for v in t]
    n = 16 + 36 * cube[0] + 6 * cube[1] + cube[2]
    g = max(0, min(23, round((sum(t) / 3 - 8) / 10)))
    return min((n, 232 + g), key=lambda i: d(rgb(i)))


class Paint:
    def __init__(self, theme, truecolor):
        self.t, self.true = theme, truecolor
        self.bg = rgb(theme['bg'])
        self.codes = {}         # (colour, strength, ground): its escape, worked out once

    def code(self, c, s, ground):
        key = (c, s, ground)
        if key not in self.codes:
            self.codes[key] = self.work_out(c, s, ground)
        return self.codes[key]

    def work_out(self, c, s, ground):
        c = self.t.get(c, c) if isinstance(c, str) and not c.startswith('#') else c
        if s >= 0.999 and isinstance(c, int):
            return f'{ground};5;{c}'     # the exact palette colour, as `ls` would show it
        r = tuple(round(b + (x - b) * s) for x, b in zip(rgb(c), self.bg))
        return f'{ground};2;{r[0]};{r[1]};{r[2]}' if self.true else f'{ground};5;{nearest(r)}'

    def fg(self, c, s=1.0, bold=False):
        return f"\033[{'1;' if bold else ''}{self.code(c, s, 38)}m"

    def on(self, fg, bg, bold=False):
        return f"\033[{'1;' if bold else ''}{self.code(fg, 1, 38)};{self.code(bg, 1, 48)}m"


def cells(text):
    """How many columns `text` takes on a terminal: CJK and the like take two, accents none."""
    if text.isascii():                  # most text: as wide as it is long
        return len(text)
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1
               for c in text)


def clip(text, room):
    """`text` cut to `room` columns, ending in … when it was cut."""
    if cells(text) <= room:
        return text
    out, used = '', 0
    for c in text:
        if used + cells(c) > room - 1:
            break
        out, used = out + c, used + cells(c)
    return out + '…'


def printable(text):
    return ''.join(c if c.isprintable() else '?' for c in text)


def wrap(text, width):
    """`text` as lines of at most `width` columns, broken at spaces where it can be. A line's
    leading spaces go on to the lines it wraps onto (a hanging indent)."""
    width = max(width, 2)
    out = []
    for para in text.replace('\r', '').expandtabs(4).split('\n'):
        indent = para[:len(para) - len(para.lstrip(' '))]
        if len(indent) > width // 2:
            indent = ''
        start = len(out)
        line = None
        for word in printable(para).split(' '):
            if line is not None and cells(line) + 1 + cells(word) <= width:
                line += ' ' + word
                continue
            if line is not None:
                out.append(line)
            lead = '' if len(out) == start else indent      # every line but a paragraph's first
            while cells(lead + word) > width:   # longer than a line on its own: cut it
                head = clip(word, width - cells(lead) + 1)[:-1]
                out.append(lead + head)
                word, lead = word[len(head):], indent
            line = lead + word
        out.append(line or '')
    return out


def truecolor_terminal():
    if os.environ.get('COLORTERM') in ('truecolor', '24bit'):
        return True
    if os.environ.get('TMUX'):
        return False   # tmux passes 24-bit colour on only when it's set up to; don't guess
    return bool(os.environ.get('WT_SESSION')) or os.environ.get('TERM_PROGRAM') in (
        'iTerm.app', 'WezTerm', 'vscode', 'ghostty')


def ls_colors():
    """LS_COLORS as {key: (colour, bold)}: 'di', 'ln', 'ex', … and '*.ext' (lower-cased). Without
    it (a program started outside your shell), the colours `ls` itself would use: dircolors's."""
    out = {}
    spec = os.environ.get('LS_COLORS', '')
    if not spec and shutil.which('dircolors'):
        try:
            said = subprocess.run(['dircolors', '-b'], capture_output=True, text=True, timeout=2).stdout
            spec = said.split("'")[1] if "'" in said else ''
        except (OSError, subprocess.SubprocessError, IndexError):
            spec = ''
    for item in spec.split(':'):
        key, _, value = item.partition('=')
        if not key or not value:
            continue
        codes, colour, bold = value.split(';'), None, False
        i = 0
        while i < len(codes):
            c = codes[i]
            if c in ('1', '01'):
                bold = True
            elif c.isdigit() and (30 <= int(c) <= 37 or 90 <= int(c) <= 97):
                colour = int(c) - 30 if int(c) < 90 else int(c) - 82
            elif c == '38' and i + 2 < len(codes) and codes[i + 1] == '5':
                colour, i = int(codes[i + 2]), i + 2
            elif c == '38' and i + 4 < len(codes) and codes[i + 1] == '2':
                colour, i = tuple(int(x) for x in codes[i + 2:i + 5]), i + 4
            i += 1
        if colour is not None or bold:
            out[key.lower() if key.startswith('*') else key] = (colour, bold)
    return out


# -- pictures in the pane

PEOPLE = 10     # person0 … person9 in every theme
PIN_ENTRIES = 11    # a pin's entries at the least (a folder's first, or an entry's neighbours); more as there's room
PIN_GAP = 4         # columns between pins on the pinboard, and rows between their bands

# -- motion: short, and none at all when calm (CDECK_CALM, or a frame drawn without animate)
PULSE = 0.9         # seconds a row's flash takes to fade back
SHOWN_OK = 3.0      # how long ✓ stays
SHOWN_FAILED = 8.0  # and ✗
SHOWN_NOTE = 5.0    # and a notice
DISSOLVE = 0.8      # what's gone turning to dust
REVEAL = 0.45       # new lines at the end of a following pane resolving out of noise
SPIN = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
SPIN_STEP = 1 / 12  # seconds a spinner's frame shows
FRAME = 1 / 60      # between frames while something moves
BLINK_FAST = 0.25   # a beacon's half-blink (lit, then not) when what it marks has just come
BLINK_SLOW = 3.0    # and when it's BLINK_FOR old: each half-blink longer than the last till then
BLINK_FOR = 3600.0  # after that, it stays lit
# a glow (glow()) by age: brightest when it's just changed, fading out in steps over a week
GLOW = ((600, 0.9), (3600, 0.7), (6 * 3600, 0.5), (86400, 0.35), (3 * 86400, 0.2), (7 * 86400, 0.1))
NOISE = '░▒▓<>/\\|_-=+*#%&$@01'     # cdeck's
DUST = '▓▒░· '
SGR_RE = re.compile(r'\x1b\[[0-9;]*m')


def chance(seed, i):
    """A steady 0..1 for character i: the same each frame, so noise settles rather than flickers."""
    return ((i * 7919 + seed * 104729) % 1009) / 1009


def scramble(line, amount, seed=0):
    """`line` (it may have colours) with about `amount` of its characters still noise."""
    out, i = [], 0
    for piece in re.split(r'(\x1b\[[0-9;]*m)', line):
        if SGR_RE.fullmatch(piece):
            out.append(piece)
            continue
        for ch in piece:
            if ch not in (' ', PLACEHOLDER) and cells(ch) == 1 and chance(seed, i) < amount:
                ch = NOISE[int(chance(seed + 1, i) * len(NOISE))]
            out.append(ch)
            i += 1
    return ''.join(out)


def dust(text, amount, seed=0):
    """`text` turning to dust: by `amount` 1 it's spaces."""
    out = []
    for i, ch in enumerate(text):
        r = chance(seed, i) * 0.6
        if amount > r and ch != ' ':
            stage = min(len(DUST) - 1, int((amount - r) / 0.4 * len(DUST)))
            ch = DUST[stage] if cells(ch) == 1 else ' ' * cells(ch)
        out.append(ch)
    return ''.join(out)


def blend(a, b, t):
    """From colour a to colour b (RGB), t from 0 to 1."""
    a, b = rgb(a), rgb(b)
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


class Chips:
    """A line of chips for the reading pane, drawn as the bar draws its path: `segs` are (text,
    fg, bg, bold) in theme roles, and `after` is plain text that follows them (a date, say)."""
    def __init__(self, segs, after=''):
        self.segs, self.after = segs, after


GRAPHICS = None     # how pictures are drawn: None, chafa's coloured blocks; 'kitty', by the terminal
                    # itself (kitty's graphics protocol). run() decides, by graphics_terminal()
CELL = (10.0, 20.0)     # a cell's size in pixels, as the terminal says (run() asks it)
PLACEHOLDER = '\U0010EEEE'
# the combining marks that number a placeholder cell's row and column: kitty's rowcolumn-diacritics.txt
DIACRITICS = ''.join(chr(c) for part in (
    '305 30D-30E 310 312 33D-33F 346 34A-34C 350-352 357 35B 363-36F 483-487 592-595 597-599 59C-5A1 '
    '5A8-5A9 5AB-5AC 5AF 5C4 610-617 657-65B 65D-65E 6D6-6DC 6DF-6E2 6E4 6E7-6E8 6EB-6EC 730 732-733 '
    '735-736 73A 73D 73F-741 743 745 747 749-74A 7EB-7F1 7F3 816-819 81B-823 825-827 829-82D 951 953-954 '
    'F82-F83 F86-F87 135D-135F 17DD 193A 1A17 1A75-1A7C 1B6B 1B6D-1B73 1CD0-1CD2 1CDA-1CDB 1CE0 1DC0-1DC1 '
    '1DC3-1DC9 1DCB-1DCC 1DD1-1DE6 1DFE 20D0-20D1 20D4-20D7 20DB-20DC 20E1 20E7 20E9 20F0 2CEF-2CF1 '
    '2DE0-2DFF A66F A67C-A67D A6F0-A6F1 A8E0-A8F1 AAB0 AAB2-AAB3 AAB7-AAB8 AABE-AABF AAC1 FE20-FE26 10A0F '
    '10A38 1D185-1D189 1D1AA-1D1AD 1D242-1D244').split()
    for a, _, b in [part.partition('-')] for c in range(int(a, 16), int(b or a, 16) + 1))
PLACED = re.compile(PLACEHOLDER + '[' + re.escape(DIACRITICS) + ']*')


def graphics_terminal(env=None):
    """'kitty' when the terminal draws pictures itself with kitty's graphics protocol and its
    Unicode placeholders (kitty, Ghostty; through tmux when tmux lets them pass), else None.
    TWIGS_IMAGES=kitty or TWIGS_IMAGES=blocks decides it outright."""
    env = os.environ if env is None else env
    said = env.get('TWIGS_IMAGES', '').lower()
    if said == 'kitty':
        return 'kitty'
    if said:
        return None
    if not (env.get('TERM') in ('xterm-kitty', 'xterm-ghostty') or env.get('KITTY_WINDOW_ID') or
            env.get('TERM_PROGRAM') == 'ghostty' or env.get('GHOSTTY_RESOURCES_DIR')):
        return None
    if env.get('TMUX'):         # passthrough must be on for the pictures to reach the terminal
        try:
            got = subprocess.run(['tmux', 'show', '-gv', 'allow-passthrough'], capture_output=True,
                                 text=True, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        return 'kitty' if got in ('on', 'all') else None
    return 'kitty'


def cell_size(fd):
    """A cell's (width, height) in pixels from the terminal's window size; None if it won't say."""
    try:
        import fcntl, struct, termios
        rows, cols, xp, yp = struct.unpack('HHHH', fcntl.ioctl(fd, termios.TIOCGWINSZ, b'\0' * 8))
    except (OSError, ImportError, AttributeError):
        return None
    return (xp / cols, yp / rows) if xp and yp and cols and rows else None


def image_size(path):
    """(width, height) of a PNG, GIF, JPEG, WebP or BMP from its header; None if it isn't one."""
    import struct
    try:
        with open(path, 'rb') as f:
            head = f.read(32)
            if head[:8] == b'\x89PNG\r\n\x1a\n':
                return struct.unpack('>II', head[16:24])
            if head[:6] in (b'GIF87a', b'GIF89a'):
                return struct.unpack('<HH', head[6:10])
            if head[:2] == b'BM':
                w, h = struct.unpack('<ii', head[18:26])
                return w, abs(h)
            if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
                kind = head[12:16]
                if kind == b'VP8X':
                    return (int.from_bytes(head[24:27], 'little') + 1, int.from_bytes(head[27:30], 'little') + 1)
                if kind == b'VP8L':
                    b = int.from_bytes(head[21:25], 'little')
                    return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
                if kind == b'VP8 ':
                    w, h = struct.unpack('<HH', head[26:30])
                    return w & 0x3FFF, h & 0x3FFF
                return None
            if head[:2] != b'\xff\xd8':
                return None
            f.seek(2)
            while True:                     # JPEG: the first start-of-frame marker says
                marker = f.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None
                if marker[1] in (0xD8, 0x01) or 0xD0 <= marker[1] <= 0xD7:
                    continue
                length = struct.unpack('>H', f.read(2))[0]
                if marker[1] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack('>HH', f.read(5)[1:5])
                    return w, h
                f.seek(length - 2, 1)
    except (OSError, struct.error):
        return None


def png_of(path, width, height):
    """The picture as PNG bytes no bigger than width × height pixels (Pillow, else ImageMagick,
    else a PNG as it is); None when it can't be made one."""
    try:
        from PIL import Image
        import io
        with Image.open(path) as im:
            im.seek(0)
            im = im.convert('RGBA' if 'A' in im.getbands() or im.mode == 'P' else 'RGB')
            im.thumbnail((max(1, width), max(1, height)))
            out = io.BytesIO()
            im.save(out, 'PNG')
            return out.getvalue()
    except ImportError:
        pass
    except Exception:           # Pillow couldn't read it: a format it doesn't know, or a broken file
        return None
    magick = shutil.which('magick') or shutil.which('convert')
    if magick:
        try:
            done = subprocess.run([magick, path + '[0]', '-resize', f'{max(1, width)}x{max(1, height)}>', 'png:-'],
                                  capture_output=True, timeout=20)
            if done.returncode == 0 and done.stdout.startswith(b'\x89PNG'):
                return done.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        with open(path, 'rb') as f:
            data = f.read()
        return data if data.startswith(b'\x89PNG\r\n\x1a\n') else None
    except OSError:
        return None


class Images:
    """Pictures the terminal draws itself (kitty's graphics protocol): each sent once, as PNG,
    with a virtual placement, and shown by placeholder cells (PLACEHOLDER, its row and column in
    combining marks, its id in their colour). To the canvas they're text like any other, so they
    scroll, clip and redraw as the lines they're in do; output() sends what a frame needs first."""
    KEEP = 48       # placements remembered; older ones leave the terminal too
    USED = re.compile('\x1b\\[38;2;(\\d+);(\\d+);(\\d+)m' + PLACEHOLDER)

    def __init__(self):
        self.placed = {}            # (path, mtime, width, height, cell): (id, lines), the latest last
        self.data = {}              # id: (png, cols, rows)
        self.sent, self.gone = set(), []
        self.next = (os.getpid() * 7919 + int(time.time())) % 0xEFFFFF + 0x100000   # unlike others' ids

    def place(self, path, width, height):
        """Placeholder lines for the picture at most width × height cells; None if it can't be."""
        try:
            key = (path, os.stat(path).st_mtime_ns, width, height, CELL)
        except OSError:
            return None
        if key in self.placed:
            self.placed[key] = self.placed.pop(key)
            return self.placed[key][1]
        size = image_size(path)
        if not size or min(size) < 1 or width < 1 or height < 1:
            return None
        (iw, ih), (cw, ch) = size, CELL
        cols = max(1, min(width, math.ceil(iw / cw)))
        rows = max(1, math.ceil(cols * cw * ih / iw / ch))
        if rows > height:
            rows = height
            cols = max(1, min(width, math.ceil(rows * ch * iw / ih / cw)))
        rows, cols = min(rows, len(DIACRITICS)), min(cols, len(DIACRITICS))
        png = png_of(path, round(cols * cw), round(rows * ch))
        if png is None:
            return None
        ident, self.next = self.next, self.next + 1 if self.next < 0xFFFFFF else 0x100000
        colour = f'\033[38;2;{ident >> 16 & 255};{ident >> 8 & 255};{ident & 255}m'
        lines = [colour + ''.join(PLACEHOLDER + DIACRITICS[r] + DIACRITICS[c] for c in range(cols)) + '\033[39m'
                 for r in range(rows)]
        self.data[ident] = (png, cols, rows)
        self.placed[key] = (ident, lines)
        while len(self.placed) > self.KEEP:
            old = self.placed.pop(next(iter(self.placed)))[0]
            self.data.pop(old, None)
            if old in self.sent:
                self.sent.discard(old)
                self.gone.append(old)
        return lines

    def cells(self, path, width, height):
        """The picture as the canvas's cells: (the style that names it, [rows of placeholders]),
        for drawing in a grid one cell at a time; None if it can't be."""
        lines = self.place(path, width, height)
        if not lines:
            return None
        style = lines[0][:lines[0].index(PLACEHOLDER)]
        return style, [l[len(style):-len('\033[39m')] for l in lines]

    @staticmethod
    def command(text):
        """kitty's escape for one graphics command; through tmux, wrapped so it passes."""
        s = f'\033_G{text}\033\\'
        return f'\033Ptmux;{s.replace(chr(27), chr(27) * 2)}\033\\' if os.environ.get('TMUX') else s

    def output(self, text):
        """What to write before `text`: the pictures it shows that the terminal hasn't got yet, and
        the deleting of ones forgotten."""
        out = [self.command(f'a=d,d=I,i={i},q=2') for i in self.gone]
        self.gone = []
        for m in self.USED.finditer(text) if PLACEHOLDER in text else ():
            ident = int(m[1]) << 16 | int(m[2]) << 8 | int(m[3])
            if ident in self.sent or ident not in self.data:
                continue
            png, cols, rows = self.data[ident]
            import base64
            data = base64.standard_b64encode(png).decode()
            chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)] or ['']
            for n, chunk in enumerate(chunks):
                more = int(n < len(chunks) - 1)
                head = f'a=T,U=1,f=100,t=d,i={ident},c={cols},r={rows},q=2,m={more}' if n == 0 else f'q=2,m={more}'
                out.append(self.command(f'{head};{chunk}'))
            self.sent.add(ident)
        return ''.join(out)

    def forget(self):
        """The screen was cleared (a resize, coming back from an editor): send them again as needed."""
        self.sent.clear()

    def clear(self):
        """Take every picture sent out of the terminal (leaving)."""
        out = ''.join(self.command(f'a=d,d=I,i={i},q=2') for i in self.sent | set(self.gone))
        self.sent.clear()
        self.gone = []
        return out


IMAGES = Images()


class Picture:
    """A picture for the reading pane: preview() returns one, or a list of text and pictures.
    In a terminal that draws pictures (GRAPHICS 'kitty'), it's the picture itself, as many cells
    as its size takes up to the pane's width and `rows` high; elsewhere chafa draws it as coloured
    blocks. Either way it scrolls with the text; without either it's a line saying what it is."""
    SGR = re.compile(r'\x1b\[[0-9;]*m')
    # any other CSI (not ending in m), OSC / DCS / PM / APC strings, and any other ESC pair
    OTHER = re.compile(r'\x1b\[[0-9;?]*[A-Za-ln-z@`]|\x1b[\]P^_][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[^\[]')

    def __init__(self, path, name=None, rows=20):
        self.path, self.name, self.rows = path, name or os.path.basename(path), rows
        self.drawn = {}

    def lines(self, width, height, truecolor=True):
        if GRAPHICS == 'kitty':
            got = IMAGES.place(self.path, width, min(height, self.rows))
            if got:
                return got
        key = (width, min(height, self.rows), truecolor)
        if key not in self.drawn:
            self.drawn[key] = self.draw(*key)
        return self.drawn[key]

    def draw(self, width, height, truecolor):
        note = ['🖼 ' + clip(printable(self.name), max(1, width - 2))]
        if not shutil.which('chafa') or width < 4 or height < 2:
            return note
        try:
            out = subprocess.run(['chafa', '--format=symbols', '--colors=' + ('full' if truecolor else '256'),
                                  f'--size={width}x{height}', '--animate=off', '--polite=on', self.path],
                                 capture_output=True, timeout=10).stdout.decode('utf-8', 'replace')
        except (OSError, subprocess.SubprocessError):
            return note
        return self.clean(out) or note

    def clean(self, out):
        """chafa's lines with only their colours: other escapes (cursor, modes) go whole, then
        stray control characters; each line ends in a reset."""
        lines = []
        for line in out.rstrip('\n').split('\n'):
            line = self.OTHER.sub('', line)
            codes, texts = self.SGR.findall(line), self.SGR.split(line)
            kept = [''.join(c for c in t if c.isprintable()) for t in texts]
            lines.append(''.join(k + (codes[i] if i < len(codes) else '') for i, k in enumerate(kept)) + '\033[0m')
        return [l for l in lines if l != '\033[0m']


# -- waking the canvas

_WAKE = None    # the running canvas's pipe: a byte written there wakes it
_RUNS = 0       # canvases running (one may open another, from an action outside())


def wake():
    """From any thread: the canvas looks again now, rather than when it next meant to (a key,
    or the next thing it knows will change). invalidate() and signal() call it."""
    w = _WAKE
    if w is not None:
        try:
            os.write(w, b'.')
        except OSError:         # full (it's awake already), or closed (it's gone)
            pass


def blink(age):
    """A beacon `age` seconds old: (lit, seconds until that changes, or None for never). Fast at
    first (BLINK_FAST on, BLINK_FAST off), each half-blink a little longer, BLINK_SLOW by
    BLINK_FOR, and lit from then on. The half-blinks' lengths grow evenly with age, h(t) = a + t/k,
    so the half-blinks so far are ∫ dt / h = k·ln(h / a): the next flip's time comes straight out."""
    age = max(0.0, age)
    if age >= BLINK_FOR:
        return True, None
    k = BLINK_FOR / (BLINK_SLOW - BLINK_FAST)
    n = int(k * math.log((BLINK_FAST + age / k) / BLINK_FAST))
    flip = (BLINK_FAST * math.exp((n + 1) / k) - BLINK_FAST) * k
    return n % 2 == 0, min(flip, BLINK_FOR) - age


def glowing(age):
    """How strongly something changed `age` seconds ago glows (0 not at all), and the age at
    which that next steps down (None: never again)."""
    for until, strength in GLOW:
        if age < until:
            return strength, until
    return 0.0, None


def update(lines, shown):
    """What to write for the terminal to show `lines`, when it shows `shown`: only the lines that
    differ, each where it goes (a blink rewrites one line, not the screen)."""
    out = [f'\033[{i + 1}H{line}\033[K' for i, line in enumerate(lines) if i >= len(shown) or shown[i] != line]
    if len(shown) > len(lines):
        out.append(f'\033[{len(lines) + 1}H\033[J')
    return '\033[?2026h' + ''.join(out) + '\033[?2026l' if out else ''


# -- git

class Git:
    """`git status` per repository, run in the background; the canvas redraws when it lands."""
    RANK = {'DD': 4, 'AU': 4, 'UD': 4, 'UA': 4, 'DU': 4, 'AA': 4, 'UU': 4}

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.roots, self.info, self.pending = {}, {}, set()
        self.lock = threading.Lock()
        self.changed = False
        self.pool = ThreadPoolExecutor(4) if enabled else None

    def root(self, d):
        walked = []
        while True:
            if d in self.roots:
                r = self.roots[d]
                break
            walked.append(d)
            if os.path.exists(os.path.join(d, '.git')):
                r = d
                break
            up = os.path.dirname(d)
            if up == d:
                r = None
                break
            d = up
        for w in walked:
            self.roots[w] = r
        return r

    def status(self, root):
        with self.lock:
            if root in self.info:
                return self.info[root]
            if root not in self.pending:
                self.pending.add(root)
                self.pool.submit(self._run, root)
        return None

    def _run(self, root):
        files, prefixes, agg, dirty = {}, {}, {}, False
        try:
            raw = subprocess.run(['git', '-C', root, 'status', '--porcelain=v1', '-z',
                                  '--ignored=matching', '-unormal'],
                                 capture_output=True, timeout=5).stdout
            parts = os.fsdecode(raw).split('\0')
            i = 0
            while i < len(parts):
                e = parts[i]
                i += 1
                if len(e) < 4:
                    continue
                xy, rel = e[:2], e[3:]
                if xy[0] in 'RC':
                    i += 1   # the name it had before
                path = os.path.join(root, rel.rstrip('/'))
                if xy == '!!':
                    prefixes[path] = 'ignored'
                    continue
                rank = 1 if xy == '??' else self.RANK.get(xy) or (3 if xy[1] in 'MDT' else 2)
                dirty = True
                files[path] = rank
                if xy == '??' and rel.endswith('/'):
                    prefixes[path] = 1
                d = os.path.dirname(path)
                while len(d) >= len(root):
                    agg[d] = max(agg.get(d, 0), rank)
                    if d == root:
                        break
                    d = os.path.dirname(d)
        except (OSError, subprocess.SubprocessError):
            pass
        with self.lock:
            self.info[root] = {'files': files, 'prefixes': prefixes, 'agg': agg, 'dirty': dirty}
            self.pending.discard(root)
            self.changed = True
        wake()

    def wait(self):
        while self.pending:
            threading.Event().wait(0.02)

    def state(self, node):
        """(rank 0-4, ignored, whether the rank is of something inside a folder)."""
        if not self.enabled or not isinstance(node, Node):
            return 0, False, False
        r = self.root(node.path if node.is_dir else os.path.dirname(node.path))
        info = r and self.status(r)
        if not info:
            return 0, False, False
        if node.path == r:
            return info['agg'].get(r, 0), False, True
        rank = info['files'].get(node.path, 0)
        p = node.path
        while len(p) > len(r):
            hit = info['prefixes'].get(p)
            if hit == 'ignored':
                return rank, True, False
            if hit and not rank:
                rank = hit
            p = os.path.dirname(p)
        if not rank and node.is_dir:
            return info['agg'].get(node.path, 0), False, True
        return rank, False, False

    def branch(self, path):
        """(name, dirty) for the repository holding `path`, or None."""
        if not self.enabled:
            return None
        r = self.root(path if os.path.isdir(path) else os.path.dirname(path))
        if not r:
            return None
        git = os.path.join(r, '.git')
        try:
            if os.path.isfile(git):
                with open(git) as f:
                    git = os.path.join(r, f.read().split('gitdir:', 1)[1].strip())
            with open(os.path.join(git, 'HEAD')) as f:
                head = f.read().strip()
        except (OSError, IndexError):
            return None
        name = head[16:] if head.startswith('ref: refs/heads/') else head[:7]
        info = self.status(r)
        return name, bool(info and info['dirty'])


# -- the tree

class Node:
    """One entry on the canvas. `path` is its id (unique in the tree, what Enter returns), `is_dir`
    that it can be opened, `kind` and `data` its provider's own: what it is and what it holds."""
    def __init__(self, path, name, is_dir, parent=None, kind='fi', source=None, data=None):
        self.path, self.name, self.is_dir, self.parent, self.kind = path, name, is_dir, parent, kind
        self.source = source or (parent.source if parent else None)   # its Provider
        self.data = data
        self.kids = None        # loaded lazily
        self.open = False
        self.sel = 0            # index into shown(): the remembered / selected child
        self.filter = ""
        self.error = None
        self.row = self.depth = 0
        self.entered = False    # opened before: it's chosen where you left it, not where it starts
        self.dirs_only = parent.dirs_only if parent else False

    def load(self):
        if self.kids is None:
            try:
                self.kids = list(self.source.children(self))
            except OSError as e:
                self.kids, self.error = [], e.strerror or "unreadable"
        return self.kids

    def shown(self):
        f = self.filter.casefold()
        kids = [k for k in self.load() if k.is_dir] if self.dirs_only else self.load()
        return [k for k in kids if f in k.name.casefold()] if f else kids

    def label(self):
        return self.source.label(self)


# -- where the tree comes from

class Provider:
    """Where a canvas's tree comes from: the canvas lays it out, draws it and moves across it; a
    provider says what's in it. Subclass it and hand one over (`pick(provider=...)`,
    `options(provider=...)`); only children() is needed, the rest have plain defaults.

    These run while drawing, so they should be quick. Slow work goes to a thread that sets
    `changed` when it lands (the canvas redraws, and clears it), with busy() true until it has;
    when what's in the tree has changed, the thread calls invalidate() instead.

    A provider can keep the canvas open: choose() returning None stays (Enter opens a branch),
    and `pane` shows preview() beside the tree, a reading pane."""
    changed = False
    pane = False      # the reading pane's default (--pane wins)
    room = None       # rows a pin may take on the pinboard, set by the canvas before reference()

    def root(self):
        return Node('', '', True, source=self)

    def children(self, node):
        """`node`'s children, Nodes with `node` as their parent, in the order they're shown. Raise
        OSError when they can't be had: its strerror is shown in their place."""
        return []

    def locate(self, start):
        """Where to begin, from the caller's `start`: the ids (paths) of the nodes from below the
        root down to it, and text to filter its children by (a half-typed name)."""
        return [], ''

    def label(self, node):
        return node.name

    def colour(self, node):
        """(colour, bold) at full strength, a theme role or a colour; None for the theme's dir / file."""
        return None

    def mark(self, node):
        """A mark beside the entry, (theme role, glyph, glyph without special fonts); or None."""
        return None

    def dim(self, node):
        """How bright the entry is beside its neighbours: 1 the same, less fainter."""
        return 1.0

    def bar(self, node, plain):
        """The bar along the bottom with `node` chosen: (segments, tail), each a list of (text,
        fg, bg, bold) in theme roles. When there's no room, segments after the first give way."""
        names = []
        while node.parent:
            names.append(self.label(node).split('\n')[0]); node = node.parent
        segs = [(self.label(node).split('\n')[0] or '⌂', 'home_fg', 'home_bg', False)]
        names.reverse()
        for i, name in enumerate(names):
            last = i == len(names) - 1
            segs.append((name, 'cwd_fg' if last else 'seg_fg', 'seg_bg', last))
        return segs, []

    def choose(self, node):
        """Enter: what the canvas ends with (pick() returns it), or None to stay on it (a branch
        opens), or STAY to stay without opening it (Enter did something to it)."""
        return node.path

    def actions(self, node):
        """Keys handled on `node`: [(key, name, hint)]. A key is a control character the canvas
        and the usual terminals leave free (⌃A ⌃D ⌃E ⌃F ⌃K ⌃L ⌃N ⌃O ⌃P ⌃R ⌃S ⌃T ⌃W ⌃Y ⌃Z: not ⌃B,
        tmux's, nor ⌃V, Windows Terminal's paste), or 'alt-up' … 'alt-left' for Alt with an arrow;
        the hint ('⌃N reply') is shown along the bottom. A key no terminal sends by itself
        ('ctrl-.') works where the terminal is told to send it as CSI u; give it a second key too.

        Each key keeps one meaning across twigs: ⌃N new (on a message: a reply to it), ⌃E edit,
        ⌃D delete, ⌃O open, ⌃S save, ⌃R refresh, ⌃L how it's listed (grouping), ⌃W when (due,
        snooze, send later, what if), ⌃P put forward (plan, flag, pin), ⌃Z undo."""
        return []

    def act(self, node, name, canvas):
        """Do the action `name` on `node`. canvas.ask() opens a typing line, canvas.outside()
        runs something with the terminal (an editor, another canvas), canvas.say() tells you how it
        went. A value ends the canvas, as choose()'s does."""
        return None

    def preview(self, node):
        """What the reading pane shows for `node`: text (wrapped to fit), or None for nothing."""
        return None

    def width(self, node):
        """Columns `node`'s label wraps to, on as many lines as it takes; None: one line, cut at
        --colmax (a name). A message, say, shows whole."""
        return None

    def picture(self, node):
        """A picture to draw in the canvas under `node`'s label (a chat's photo): its file's path,
        or None. Only where the terminal draws pictures itself (GRAPHICS); elsewhere the pane."""
        return None

    def picture_rows(self, node):
        """How many rows high picture() may be drawn."""
        return 12

    def chip(self, node):
        """(fg, bg) to draw `node` as a chip, as the bar draws its path; None for plain text."""
        return None

    def reference(self, node):
        """What a pin keeps in view on the pinboard: `root` (the branch it hangs from), `nodes`
        (what's shown under it), `width` (to wrap labels to; None: as they are) and `labels`
        ({path: text} drawn instead). An open folder, with what's in it; anything else, among
        its neighbours. A provider says more for its own (a message's text, a chat's latest)."""
        many = max(PIN_ENTRIES, self.room or 0)         # as many as there's room for
        if node.is_dir and node.open or node.parent is None:
            return dict(root=node, nodes=node.shown()[:many], width=None, labels={})
        items = node.parent.shown()
        at = items.index(node) if node in items else 0
        start = max(0, min(at - many // 2, len(items) - many))
        return dict(root=node.parent, nodes=items[start:start + many], width=None, labels={})

    def chip_rows(self, node):
        """How many label lines wear the chip; None means the whole label."""
        return None

    def count(self, node):
        """A number for `node` (unread mail, say), (text, colour, bold): the column of them is
        right-aligned after the column's names, so they line up to be read down. None for none."""
        return None

    def count_beside(self, node):
        """True to draw count() right after `node`'s own label instead (an address beside its
        person), where a column of long lines would carry it out of sight."""
        return False

    def copy_text(self, node):
        """What Ctrl+Shift+C copies with `node` under the cursor: its label, by default; None for
        nothing."""
        return node.name

    def beacon(self, node):
        """When what `node` marks came (time.time()), to draw it as a beacon: white on red, blinking
        fast at first, slower as it ages, then steadily lit (lit throughout when calm); None for
        none. Something new and unread, say: once it's read, return None and invalidate()."""
        return None

    def glow(self, node):
        """When what `node` is last changed (time.time()), for it to glow: its label tinted towards
        the theme's glow, brightest when it's just changed and fading over a week (GLOW). None for
        no glow. Files, by when they were last modified."""
        return None

    def next_change(self):
        """When (time.time()) what's drawn next changes by itself, with nothing to tell the canvas
        (a countdown ticking over, a clock): it draws again then. None: only when told."""
        return None

    def tint(self, node):
        """The start of `node`'s label in colours of its own: [(how many characters, colour, bold)],
        in order from its first (a date's day and weekday, a time); [] for the label's own colour.
        A colour is a theme role or a colour ('#ff8c2a', 208). Not on the choice or a chip."""
        return []

    def mark_before(self, node):
        """Whether `node`'s mark goes before its label (a task's ✓), not after (a file's git ●).
        A column with any mark before keeps room for one, so its labels line up."""
        return False

    def line(self, node):
        """A colour for the branch line that leads into `node` (its state, say); None: the canvas's."""
        return None

    def start_at(self, node):
        """Which of `node`'s children the canvas lands on the first time it's opened: an index,
        or -1 for the last, which also keeps the choice on the last as more come (a conversation)."""
        return 0

    def pane_for(self, node):
        """Whether the reading pane shows while `node` is chosen (when there is one)."""
        return True

    def preview_end(self, node):
        """Whether the pane opens on the end of `node`'s preview (a conversation's newest lines,
        a log's latest), and keeps to the end as it grows while you're there."""
        return False

    def reach(self, node):
        """Whether `node`'s children are quick to have, so jump (⌃Space) may look inside a branch
        that hasn't been opened yet. A tree kept whole and near (tasks, a calendar) says True; one
        that's fetched or endless (mail, the file system) leaves it False: only what's open is searched."""
        return False

    def invalidate(self, path=None, deep=False):
        """From any thread: fetch the children of the node with id `path` (of every node, with
        None) again before the next frame. What's open and chosen stays where the ids still are.
        `deep`: and the children of everything loaded under it, a branch whose own branches follow
        from it (a conversation's runs, which a new message joins)."""
        with STALE:
            self.__dict__.setdefault('_stale', set()).add((path, 'deep') if deep and path else path)
        self.changed = True
        wake()

    def stale(self):
        """The ids invalidate() was given since last asked."""
        with STALE:
            return self.__dict__.pop('_stale', set())

    def pulse(self, path):
        """From any thread: the row of the node with id `path` flashes once (something arrived)."""
        self.signal(('pulse', path))

    def status(self, key, text, state='busy'):
        """From any thread: a chip in the bar, one per `key`. 'busy' spins until the same key comes
        back 'ok' (✓, gone after a few seconds), 'failed' (✗, for longer) or 'gone' (it dissolves);
        'note' is a notice that stays a few seconds."""
        self.signal(('status', key, text, state))

    def focus(self, node):
        """The canvas's choice has moved to `node` (told once per move): where you're looking."""

    def signal(self, what):
        with STALE:
            self.__dict__.setdefault('_signals', []).append(what)
        self.changed = True
        wake()

    def signals(self):
        """What pulse() and status() were given since last asked."""
        with STALE:
            return self.__dict__.pop('_signals', [])

    def busy(self):
        return False

    def wait(self):
        while self.busy():
            threading.Event().wait(0.02)


STALE = threading.Lock()
STAY = object()     # choose(): stay on the canvas, and don't open the branch either


def human_size(size):
    """A file's size: 812 B, 34 KB, 2.1 MB, 1.4 GB."""
    for unit, step in (('B', 1), ('KB', 1024), ('MB', 1024 ** 2), ('GB', 1024 ** 3)):
        if size < step * 1024 or unit == 'GB':
            return f'{size / step:,.0f} {unit}' if unit in ('B', 'KB') else f'{size / step:,.1f} {unit}'


class Thumbnails:
    """Pictures of what isn't one, made in the background and kept: a PDF's first page
    (pdftoppm), a video's frame (ffmpegthumbnailer), an image Pillow can't read (ImageMagick)."""
    def __init__(self):
        self.folder = os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'),
                                   'twigs', 'thumbnails')
        self.lock, self.making = threading.Lock(), set()

    @staticmethod
    def maker(path, kind):
        """How to make one of `path` (a thumbnail's path, without .png, is appended): None if nothing here can."""
        if kind == 'application/pdf' and shutil.which('pdftoppm'):
            return lambda out: ['pdftoppm', '-png', '-f', '1', '-l', '1', '-scale-to', '1200', '-singlefile', path, out]
        if kind.startswith('video/') and shutil.which('ffmpegthumbnailer'):
            return lambda out: ['ffmpegthumbnailer', '-i', path, '-o', out + '.png', '-s', '1024']
        magick = shutil.which('magick') or shutil.which('convert')
        if kind.startswith('image/') and magick:
            return lambda out: [magick, path + '[0]', '-resize', '1600x1600>', out + '.png']
        return None

    def get(self, path, kind, done):
        """The thumbnail's path when it's made; None while it's being made (done() when it is, from
        another thread); False when it can't be."""
        make = self.maker(path, kind)
        if make is None:
            return False
        try:
            st = os.stat(path)
        except OSError:
            return False
        import hashlib
        out = os.path.join(self.folder, hashlib.sha1(f'{path}\0{st.st_mtime_ns}\0{st.st_size}'.encode()).hexdigest())
        if os.path.exists(out + '.png'):
            return out + '.png'
        if os.path.exists(out + '.failed'):
            return False
        with self.lock:
            if out in self.making:
                return None
            self.making.add(out)

        def work():
            try:
                os.makedirs(self.folder, exist_ok=True)
                subprocess.run(make(out), stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass
            if not os.path.exists(out + '.png'):
                try:
                    open(out + '.failed', 'w').close()
                except OSError:
                    pass
            with self.lock:
                self.making.discard(out)
            done()
        threading.Thread(target=work, daemon=True).start()
        return None


THUMBNAILS = Thumbnails()


class FileSystem(Provider):
    """The file system: folders first, dotfiles after, LS_COLORS, git's marks and branch."""
    def __init__(self, git=True, ls=False, recent=True):
        self.git = Git(git)
        self.ls = ls_colors() if ls else {}
        self.recent = recent        # recently changed files glow

    @property
    def changed(self):
        return self.git.changed

    @changed.setter
    def changed(self, value):
        self.git.changed = value

    def root(self):
        return Node('/', '/', True, kind='di', source=self)

    def children(self, node):
        out = []
        with os.scandir(node.path) as it:
            for e in it:
                is_dir, key = kind(e)
                try:
                    changed = e.stat(follow_symlinks=False).st_mtime if self.recent else None
                except OSError:
                    changed = None
                out.append(Node(e.path, e.name, is_dir, node, key, data=changed))
        out.sort(key=lambda n: (not n.is_dir, n.name.startswith('.'), n.name.casefold()))
        return out

    def locate(self, start):
        # A start that doesn't exist yet is a half-typed path: its folder, filtered by the rest.
        start = os.path.abspath(os.path.expanduser(start or '.'))
        typed = ''
        if not os.path.exists(start):
            start, typed = os.path.dirname(start), os.path.basename(start)
        parts = [p for p in start.split('/') if p]
        return ['/' + '/'.join(parts[:i + 1]) for i in range(len(parts))], typed

    def label(self, node):
        return node.name + ('/' if node.is_dir and node.name != '/' else '')

    def colour(self, n):
        if not self.ls:
            return None
        hit = self.ls.get(n.kind)
        if not hit and n.kind in ('fi', 'ex'):
            dot = n.name.lower()
            while '.' in dot and not hit:
                dot = dot[dot.index('.') + 1:]
                hit = self.ls.get('*.' + dot)
            hit = hit or (self.ls.get('ex') if n.kind == 'ex' else None) or self.ls.get('fi')
        if hit and hit[0] is not None:
            c, bold = hit
            # Bold basic colours show as their bright versions in most terminals; dim from those.
            return (c + 8 if bold and isinstance(c, int) and c < 8 else c), bold
        if hit:
            return ('dir' if n.is_dir else 'file'), hit[1]
        return None

    def glow(self, node):
        return node.data if self.recent and not node.name.startswith('.') else None

    def mark(self, node):
        rank, _, inside = self.git.state(node)
        if not rank:
            return None
        role, glyph, ascii_ = MARKS[rank]
        return (role, '•', '*') if inside else (role, glyph, ascii_)

    def dim(self, node):
        s = 0.75 if node.name.startswith('.') else 1.0
        return s * 0.65 if self.git.state(node)[1] else s   # ignored by git

    def bar(self, node, plain):
        path = node.path
        home = os.path.expanduser('~')
        segs = []
        if path == home or path.startswith(home + '/'):
            segs.append(('~', 'home_fg', 'home_bg', False))
            rest = path[len(home):].strip('/')
        else:
            segs.append(('/', 'seg_fg', 'seg_bg', False))
            rest = path.strip('/')
        parts = rest.split('/') if rest else []
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            segs.append((part, 'cwd_fg' if last else 'seg_fg', 'seg_bg', last))
        tail = []
        b = self.git.branch(path)
        if b:
            name, dirty = b
            tail.append(((' ' if not plain else '') + name,
                         'repo_dirty_fg' if dirty else 'repo_clean_fg',
                         'repo_dirty_bg' if dirty else 'repo_clean_bg', False))
        return segs, tail

    def preview(self, node):
        if node.is_dir:
            return None
        shown = self.picture_preview(node)
        if shown is not None:
            return shown
        try:
            with open(node.path, 'rb') as f:
                head = f.read(32768)
        except OSError as e:
            return '(' + (e.strerror or 'unreadable') + ')'
        if b'\0' in head:
            return f'(binary, {os.path.getsize(node.path):,} bytes)'
        return head.decode(errors='replace')

    def picture_preview(self, node):
        """A picture, a PDF or a video in the pane as a picture (its size, then it), drawn by the
        terminal or as chafa's blocks; None for anything else."""
        import mimetypes
        path = node.path
        kind = mimetypes.guess_type(path)[0] or ''
        size = image_size(path)
        if not (size or kind.startswith(('image/', 'video/')) or kind == 'application/pdf'):
            return None
        try:
            about = human_size(os.path.getsize(path))
        except OSError as e:
            return '(' + (e.strerror or 'unreadable') + ')'
        if size:
            about = f'{size[0]} × {size[1]} · {about}'
        if size:                        # a kind it reads (PNG, JPEG, GIF, WebP, BMP): itself
            return [about, '', Picture(path, rows=200)]
        got = THUMBNAILS.get(path, kind or 'image/', lambda: self.invalidate(os.path.dirname(path) or '/'))
        if got is None:
            return [about, '', '(making a preview…)']
        if got is False:
            return None if not kind.startswith('image/') else [about, '', '(no preview: install ImageMagick)']
        return [about, '', Picture(got, os.path.basename(path), rows=200)]

    def busy(self):
        return bool(self.git.pending)

    def wait(self):
        self.git.wait()


class Tree(Provider):
    """Nested dicts as a tree, for trying the canvas on anything: a dict is a branch of
    {name: subtree}, anything else a leaf holding it (node.data). Ids are /-joined names.

        pick(provider=Tree({'fruit': {'apple': 1, 'pear': 2}, 'bread': None}), start='fruit')

    The reading pane shows a leaf's value (a list's items one to a line)."""
    pane = True

    def __init__(self, data, name='⌂'):
        self.data, self.name = data, name

    def root(self):
        return Node('', self.name, True, source=self, data=self.data)

    def children(self, node):
        d = self.data   # from the top, so a branch replaced since is seen (invalidate)
        for part in node.path.split('/')[1:]:
            d = d.get(part) if isinstance(d, dict) else None
        return [Node(f'{node.path}/{k}', str(k), isinstance(v, dict), node, data=v)
                for k, v in (d.items() if isinstance(d, dict) else ())]

    def locate(self, start):
        parts = [p for p in (start or '').split('/') if p and p != '.']
        return ['/' + '/'.join(parts[:i + 1]) for i in range(len(parts))], ''

    def preview(self, node):
        if node.is_dir or node.data is None:
            return None
        return '\n'.join(map(str, node.data)) if isinstance(node.data, list) else str(node.data)


def kind(entry):
    """(is_dir, LS_COLORS key) for a directory entry."""
    try:
        link = entry.is_symlink()
        st = entry.stat()
    except OSError:
        return False, 'or'           # a link to nothing
    if stat.S_ISDIR(st.st_mode):
        return True, 'ln' if link else 'di'
    if link:
        return False, 'ln'
    for test, key in ((stat.S_ISFIFO, 'pi'), (stat.S_ISSOCK, 'so'), (stat.S_ISBLK, 'bd'), (stat.S_ISCHR, 'cd')):
        if test(st.st_mode):
            return False, key
    return False, 'ex' if st.st_mode & 0o111 else 'fi'


class Stub:
    """A stand-in row: '↓ 12 more', '(empty)'."""
    is_dir, open, kids, name = False, False, [], ''

    def __init__(self, text, parent):
        self.text, self.parent, self.row, self.depth = text, parent, 0, 0

    def label(self):
        return self.text


def window(node, limit, anchors=None):
    """The children drawn for an open node: all of them, or at most `limit` around the selected one."""
    items = node.shown()
    if not items:
        return [Stub('(' + (node.error or ('no match' if node.filter else 'empty')) + ')', node)]
    if anchors is not None and items:
        # Keep a little context at every reference, collapsing the rows between them.
        centers = [i for i, k in enumerate(items) if id(k) in anchors]
        centers.append(max(0, min(node.sel, len(items) - 1)))
        keep = {j for i in centers for j in range(max(0, i - 1), min(len(items), i + 2))}
        out, previous = [], -1
        for i in sorted(keep):
            if i > previous + 1:
                out.append(Stub(f'… {i - previous - 1} more', node))
            out.append(items[i])
            previous = i
        if previous < len(items) - 1:
            out.append(Stub(f'… {len(items) - previous - 1} more', node))
        return out
    if not limit or len(items) <= limit:
        return items
    start = max(0, min(node.sel - limit // 2, len(items) - limit))
    out = items[start:start + limit]
    if start:
        out = [Stub(f'↑ {start} more', node)] + out[1:]
    if start + limit < len(items):
        out = out[:-1] + [Stub(f'↓ {len(items) - start - limit + 1} more', node)]
    return out


def is_open(n, focus, peek):
    return n.is_dir and (n.open or (peek and n is focus))


def layout(n, opts, focus, tall=lambda n: 1, anchors=None):
    """Rows relative to `n` for its subtree; returns its contour: [(top, bottom)] per depth.
    tall(node): how many lines it takes."""
    h = tall(n)
    if not is_open(n, focus, opts.peek) or anchors is not None and id(n) not in anchors:
        n._shown = []
        return [(0, h - 1)]
    kids = window(n, opts.limit, anchors)
    n._shown = kids
    acc, ys = [], []
    for k in kids:
        c = layout(k, opts, focus, tall, anchors)
        y = ys[-1] + 1 if ys else 0
        for d in range(min(len(acc), len(c))):
            # Keep clear of what's already placed at every depth; a blank row between cousins.
            y = max(y, acc[d][1] - c[d][0] + 1 + (1 if d else 0))
        for d, (t, b) in enumerate(c):
            if d < len(acc):
                acc[d] = (min(acc[d][0], t + y), max(acc[d][1], b + y))
            else:
                acc.append((t + y, b + y))
        ys.append(y)
    shift = -((ys[0] + ys[-1]) // 2)    # centred on the parent's row, by their first lines
    for k, y in zip(kids, ys):
        k._rel = y + shift
    return [(0, h - 1)] + [(t + shift, b + shift) for t, b in acc]


def place(n, row, depth, focus, opts, out):
    n.row, n.depth = row, depth
    out.append(n)
    if is_open(n, focus, opts.peek):
        for k in n._shown:
            place(k, row + k._rel, depth + 1, focus, opts, out)


class Canvas:
    def __init__(self, start, opts):
        self.opts = opts
        self.theme = opts.colours
        self.paint = Paint(self.theme, opts.truecolor)
        self.glyphs = GLYPHS[opts.glyphs]
        self.source = opts.provider or FileSystem(opts.git, opts.ls_colors, opts.recent)
        self.root = self.source.root()
        self.root.dirs_only = opts.dirs_only
        ids, typed = self.source.locate(start)
        node = self.root
        for want in ids:
            node.open = True
            for k in node.load():
                if k.path == want:
                    node.sel, node = node.shown().index(k) if k in node.shown() else 0, k
                    break
        if node.is_dir:
            node.open = True
            node.filter = typed
            # on the start folder itself (choosing a folder), or on what's in it
            self.focus = node if opts.on_start and not typed or not node.shown() else node.shown()[0]
        else:
            self.focus = node
        self.backdrop = opts.backdrop if isinstance(opts.backdrop, dict) else {}
        self.layer = None     # (child canvas, continuation, title), on this terminal
        self.bookmarks = {}   # slot: (tuple of ids from the root, short label, pinned open)
        self.pins_file = None   # where the pins are kept between runs (run() sets it)
        self.cam = None       # (x, y) world position of the top-left corner, floats
        self.target = None
        self.pane = self.source.pane if opts.pane is None else opts.pane
        self.scroll = 0                     # the reading pane's first line
        self.page = 10                      # and how many it shows
        self._preview = (None, 0, None)     # (node, width, its lines) last shown there
        self._at_end = False                # the pane showed its last line
        self.asking = None                  # (prompt, text, done, choices, changed): the typing line, open
        self.attaching = None               # attach(paths, text) for files going with it, if they can
        self.cursor = 0                     # and where in its text typing goes
        self.clock = time.monotonic         # for motion (tests set their own)
        self.wall = time.time               # for beacons and next_change(), which are dated
        self.flowing = False                # the last frame was mid-motion: the next must follow
        self.next_beacon = None             # seconds from the last frame to a beacon's next flip
        self.next_glow = None               # and to a glow's next step down
        self.pulses = {}                    # node id: when its row began to flash
        self.statuses = {}                  # key: (text, state, since): chips in the bar
        self.reveal = None                  # (node, first new line, since) in a following pane
        self.animate = True                 # the frame being drawn moves (not calm)
        self.told = None                    # the focus the provider was last told of
        self.going_in = None                # a node → was pressed on while its children were coming
        self.message = None                 # what say() said, until the next key
        self.waiting = []                   # what outside() was given, to run with the terminal
        self.keys_shown = False             # F12: every key there is here, in a panel over the canvas
        self.jumping = None                 # ⌃Space: {text, sel, index, hits}, the panel of what matches

    # -- for actions
    def ask(self, prompt, done, text='', choices=None, changed=None, attach=None):
        """Open the typing line along the bottom: Enter calls done(text), Esc closes it. With
        `choices` (letters, 'yne'), one of those keys answers at once: done(letter). What done()
        returns ends the canvas unless it's None; it may ask again. changed(text) hears each
        edit, and changed(None) the line closing, however it closes. With attach(paths, text),
        files can go with what's typed: pasting their paths (or dropping them on the terminal),
        or ⌃V for what the clipboard holds (a picture, files copied); attach() asks again."""
        self.asking = (prompt, text, done, choices, changed)
        self.attaching = attach
        self.cursor = len(text)             # where typing goes: at the end, to begin with

    def status(self, key, text, state='busy'):
        """A chip in the bar, as Provider.status(), from an action."""
        self.statuses[key] = (text, state, self.clock())

    def take_signals(self):
        now = self.clock()
        for what in self.source.signals():
            if what[0] == 'pulse':
                self.pulses[what[1]] = now
            elif what[0] == 'status':
                self.statuses[what[1]] = (what[2], what[3], now)

    def in_motion(self, now):
        """Forget motion that's over; whether any is left (the frame after this must be drawn too)."""
        self.pulses = {k: t for k, t in self.pulses.items() if now - t < PULSE}
        last = {'ok': SHOWN_OK, 'failed': SHOWN_FAILED, 'gone': DISSOLVE, 'note': SHOWN_NOTE}
        self.statuses = {k: v for k, v in self.statuses.items() if v[1] not in last or now - v[2] < last[v[1]]}
        if self.reveal and now - self.reveal[2] >= REVEAL:
            self.reveal = None
        return bool(self.pulses or self.statuses or self.reveal)

    def due(self):
        """Seconds until the canvas must draw again by itself: FRAME while something moves, else
        until the next thing that changes on its own (a spinner's step, a chip going, a beacon's
        blink, the provider's next_change()); None when nothing will, until a key or a wake()."""
        if self.flowing:
            return FRAME
        now, waits = self.clock(), []
        if self.layer:
            waits.append(self.layer[0].due())
        last = {'ok': SHOWN_OK, 'failed': SHOWN_FAILED, 'note': SHOWN_NOTE}
        for _, state, since in self.statuses.values():
            if state == 'busy':
                waits.append(SPIN_STEP if self.animate else None)
            elif state in last:
                waits.append(since + last[state] - now)
        if self.source.busy():
            waits.append(SPIN_STEP)
        for w in (self.next_beacon, self.next_glow):
            if w is not None:
                waits.append(w)
        at = self.source.next_change()
        if at is not None:
            waits.append(at - self.wall())
        waits = [w for w in waits if w is not None]
        return max(0.0, min(waits)) if waits else None

    def say(self, text):
        """A line in the bar until the next key: what an action did."""
        self.message = text

    def open_layer(self, start, done, title='Choose a file'):
        settings = dict(provider=FileSystem(self.opts.git, self.opts.ls_colors, self.opts.recent),
                        backdrop='off', theme=self.theme, glyphs=self.opts.glyphs,
                        color=self.opts.color, ease=self.opts.ease, pane=False)
        child = Canvas(start, options(**settings))
        self.layer = (child, done, title)

    def changed(self):
        providers = [self.source] + ([self.layer[0].source] if self.layer else [])
        changed = False
        for p in providers:
            if p.changed:
                p.changed, changed = False, True
        return changed

    def outside(self, fn):
        """Run fn() with the terminal to itself, after this key: the canvas steps aside and comes
        back. fn runs programs with terminal() (an editor, a pager) or pick()s on another canvas."""
        self.waiting.append(fn)

    def drain(self):
        """Run what outside() was given (run() does, with the terminal set aside)."""
        while self.waiting:
            fn = self.waiting.pop(0)
            try:
                fn()
            except Exception as e:      # an action that fails says so; it doesn't take the canvas down
                self.say(f'failed: {e}')

    # -- what changed underneath
    def refresh(self):
        """Fetch again what the provider invalidated, keeping what's open and chosen by id."""
        stale = self.source.stale()
        if not stale:
            return
        p = self.focus.parent       # on the last of a column that keeps to its last: stay on it
        follow = p is not None and p.kids is not None and self.source.start_at(p) == -1 and \
            bool(p.shown()) and self.focus is p.shown()[-1]
        deep = {p[0] for p in stale if isinstance(p, tuple)}
        todo = [(self.root, False)]
        while todo:
            n, under = todo.pop()           # under: below a node fetched again deep
            if n.kids is None:
                continue
            if under or None in stale or n.path in stale or n.path in deep:
                self.reload(n)
            todo.extend((k, under or n.path in deep) for k in n.kids)
        self._preview = (self._preview[0], -1, self._preview[2])   # fetched again (-1), scrolled where it was
        self.reattach()
        if follow and self.focus.parent is p and p.shown():
            p.sel, self.focus = len(p.shown()) - 1, p.shown()[-1]
        if self.going_in is not None:
            if self.going_in is self.focus and self.focus.shown():
                self.enter()
            elif self.going_in is not self.focus:
                self.going_in = None

    def reload(self, node):
        old = {k.path: k for k in node.kids}
        node.kids, node.error = None, None
        kids = []
        for k in node.load():
            o = old.get(k.path)
            if o:   # still there: the same node, with what it holds now
                o.name, o.is_dir, o.kind, o.data = k.name, k.is_dir, k.kind, k.data
                o.open = o.open and o.is_dir
                k = o
            kids.append(k)
        node.kids = kids

    def reattach(self):
        """After a reload: the focus back on the tree, beside where it was if it's gone."""
        chain, n = [], self.focus
        while n.parent:
            chain.append(n); n = n.parent
        for k in reversed(chain):
            if not any(k is x for x in n.kids or ()):
                shown = n.shown()
                self.focus = shown[min(n.sel, len(shown) - 1)] if shown else n
                break
            n = k
        p = self.focus.parent
        if p and self.focus in p.shown():
            p.sel = p.shown().index(self.focus)

    # -- moving
    def go(self, ids):
        """Choose the node at the end of `ids` (the ids from below the root down to it), opening the
        way there as a link would, a filter in the way cleared. False if it isn't there."""
        node = self.root
        for want in ids:
            hit = next((k for k in node.load() if k.path == want), None)
            if hit is None:
                return False
            node.open = node.entered = True
            if not any(k is hit for k in node.shown()):
                node.filter = ''
            shown = node.shown()
            node.sel = next((i for i, k in enumerate(shown) if k is hit), 0)
            node = hit
        self.focus = node
        return True

    def chain(self, node):
        ids = []
        while node.parent is not None:
            ids.append(node.path)
            node = node.parent
        return tuple(reversed(ids))

    def bookmark_node(self, ids):
        node = self.root
        for path in ids:
            node = next((k for k in node.load() if k.path == path), None)
            if node is None:
                # A provider may regroup a stable id under a different date/sender branch.
                stack = [self.root]
                while stack:
                    at = stack.pop()
                    if at.path == ids[-1]:
                        return at
                    stack.extend(at.kids or ())
                return None
        return node

    def bookmark(self, slot, replace=False):
        """F1–F9: pin what's under the cursor into an empty slot, or go to what's pinned there.
        Shift: unpin it, from on the pin or anywhere inside it; from elsewhere, move the pin here
        (so Shift twice always unpins)."""
        self.refresh()
        moved = slot in self.bookmarks
        if moved and replace:
            pinned, at = self.bookmark_node(self.bookmarks[slot][0]), self.focus
            while pinned is not None and at is not None and at is not pinned:
                at = at.parent
            if pinned is not None and at is pinned:
                del self.bookmarks[slot]
                self.save_pins()
                self.say(f'F{slot} unpinned · F{slot} pins here')
                return
        if replace or slot not in self.bookmarks:
            if isinstance(self.focus, Stub):
                return
            opened = self.focus.is_dir
            if opened:              # a folder is pinned with what's in it
                self.focus.open = True
            self.bookmarks[slot] = (self.chain(self.focus), self.focus.label().splitlines()[0], opened)
            self.save_pins()
            self.say(f'F{slot} moved here · Shift+F{slot} again unpins' if moved else
                     f'F{slot} pinned · Shift+F{slot} unpins · F10 clears all')
            return
        ids, label = self.bookmarks[slot][:2]
        node = self.bookmark_node(ids)
        if node is None:
            self.say(f'F{slot}: {label} is unavailable · Shift+F{slot} replaces it')
        else:
            self.go(self.chain(node))
            self.scroll = 0
            self.source.pulse(node.path)

    def save_pins(self):
        """The pins, for the next time (when this canvas keeps them: run() says where)."""
        if not self.pins_file:
            return
        data = {str(k): dict(ids=list(v[0]), label=v[1], open=v[2]) for k, v in self.bookmarks.items()}
        try:
            os.makedirs(os.path.dirname(self.pins_file), exist_ok=True)
            temp = self.pins_file + '.tmp'
            with open(temp, 'w') as f:
                json.dump(data, f, indent=1)
            os.replace(temp, self.pins_file)
        except OSError:
            pass

    def load_pins(self, path):
        """Keep the pins in `path` from now on, starting with those it holds."""
        self.pins_file = path
        try:
            with open(path if os.path.exists(path) else earlier_pins(path)) as f:
                data = json.load(f)
            self.bookmarks = {int(k): (tuple(v['ids']), v.get('label', ''), bool(v.get('open')))
                              for k, v in data.items() if k.isdigit() and 1 <= int(k) <= 9 and v.get('ids')}
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def reference_nodes(self):
        out = {}
        for slot, (ids, _, opened) in sorted(self.bookmarks.items()):
            n = self.bookmark_node(ids)
            if n is not None:
                if opened and n.is_dir:
                    n.open = True
                out[slot] = n
        return out

    def reference_paths(self, references):
        on = set()
        for n in [self.focus, *references.values()]:
            while n:
                on.add(id(n))
                if n.parent is not None:
                    n.parent.open = True
                n = n.parent
        return on

    def reference_specs(self, references):
        specs = []
        targets = list(references.values())
        for n in references.values():
            while n is not None and n is not self.focus:
                n = n.parent
            if n is self.focus:
                break
        else:
            targets.append(self.focus)
        for n in targets:
            spec = self.source.reference(n)
            same = next((s for s in specs if s['root'] is spec['root']), None)
            if same:
                same['nodes'] += [n] + spec['nodes']
                same['labels'].update(spec['labels'])
            else:
                spec['nodes'] = [n] + spec['nodes']
                specs.append(spec)
        # A reference above another already includes its path; keep just the outer branch.
        out = []
        roots = {id(s['root']) for s in specs}
        for spec in specs:
            parent = spec['root'].parent
            while parent and id(parent) not in roots:
                parent = parent.parent
            if parent is None:
                out.append(spec)
            else:
                outer = next(s for s in specs if s['root'] is parent)
                outer['nodes'] += [spec['root']] + spec['nodes']
                outer['labels'].update(spec['labels'])
        return out          # in slot order, the focus's own last: pins keep their places

    def pin_specs(self, references, w, h):
        """reference_specs() with each pin given the room there is: the height of the screen when
        the pins lie in one band across it, a share of it when they wrap into more."""
        view = h - 2 - (1 if self.opts.header else 0)          # under the bar
        fit = lambda bands: max(PIN_ENTRIES, (view - (bands - 1) * (PIN_GAP // 2)) // bands - 5)
        self.source.room = fit(1)           # 5: a pin's heading (two lines), '│', '… N more' either end
        specs = self.reference_specs(references)
        if len(specs) < 2:
            return specs
        x, bands = 2, 1             # as spread_references() will lay them out: the same lanes
        for spec in specs:
            root, wrap_to = spec['root'], spec.get('width')
            lane = 8
            for n in [root, *spec['nodes']]:
                steps, at = 0, n
                while at is not None and at is not root:
                    steps, at = steps + 1, at.parent
                text = spec['labels'].get(n.path, n.label())
                if n is root:           # headed by where it is, and its slot
                    where = self.crumbs(root, max(12, self.opts.colmax - 8))
                    text = '[F1] ' + (where + '\n' if where else '') + text
                width = wrap_to or (None if isinstance(n, Stub) else self.source.width(n))
                widest = max(min(cells(l), width or self.opts.colmax) for l in text.split('\n'))
                lane = max(lane, min(4, steps * 2) + widest + 4)
            lane = max(lane, 2 + 14 + 4)        # '… 12 more', where it leaves some out
            if x > 2 and x + lane > w - 2:
                x, bands = 2, bands + 1
            x += lane + PIN_GAP
        if bands > 1:
            self.source.room = fit(bands)
            specs = self.reference_specs(references)
        return specs

    def spread_references(self, nodes, specs, lines_of, width, height):
        """The pinboard: each pin's branch whole, side by side in slot order, in bands down the
        canvas as wide as the screen. Its root is headed by where it is (`crumbs`); what hangs
        above the pins isn't drawn. {id(node): x}; those left out aren't drawn."""
        positions = {}
        x, band_y, band_bottom = 2, 0, 0
        for spec in specs:
            root = spec['root']
            group = []
            for n in nodes:
                at = n
                while at and at is not root:
                    at = at.parent
                if at is root:
                    group.append(n)
            indent = lambda n: min(4, (n.depth - root.depth) * 2)
            lane = max((indent(n) + max(cells(l) for l in lines_of(n)) + 4 for n in group), default=8)
            if x > 2 and x + lane > width - 2:          # a new band, under the tallest of this one
                x, band_y = 2, band_bottom + PIN_GAP // 2
            y = band_y
            for n in group:
                positions[id(n)] = x + indent(n)
                n.row = y
                y += len(lines_of(n)) + (1 if n is root else 0)
            band_bottom = max(band_bottom, y)
            x += lane + PIN_GAP
        return positions

    @staticmethod
    def crumbs(node, room=30):
        """Where `node` is, for its pin's heading: 'Emails › Inbox', the nearest kept when
        it's long ('… › lab › twigs')."""
        names, n = [], node.parent
        while n is not None and n.parent is not None:
            names.append(clip(n.label().split('\n')[0], 18))
            n = n.parent
        out = ''
        for i, name in enumerate(names):        # from the nearest up, while there's room
            more = name + (' › ' + out if out else '')
            if out and cells(('… › ' if i < len(names) - 1 else '') + more) > room:
                return '… › ' + out
            out = more
        return out

    # -- jump: to anything by name
    def jump_open(self):
        self.jumping = dict(text='', sel=0, hits=[], index=self.jump_index())

    def jump_index(self, most=30000):
        """What jump looks through, in the tree's order: [(node, its name, where it is)], names
        folded for matching. Every branch already loaded, and those the provider says are quick
        to have (reach()); a branch that would have to be fetched is left as it is."""
        out, stack, where = [], [self.root], {id(self.root): ''}
        while stack and len(out) < most:
            n = stack.pop()
            if n is not self.root:
                out.append((n, self.jump_name(n), where[id(n)]))
            if not n.is_dir or (n.kids is None and not self.source.reach(n)):
                continue
            inside = where[id(n)] + (' › ' if where[id(n)] else '') + self.jump_name(n) if n is not self.root else ''
            for k in reversed(n.load()):
                where[id(k)] = inside
                stack.append(k)
        return out

    @staticmethod
    def jump_name(node):
        return printable(node.label().split('\n')[0]).strip()

    def jump_hits(self, text, most=200):
        """The entries `text` finds: every word of it in the name, or some in the name and the
        rest in where it is ('shelves apartment'). A word that begins one comes first, then the
        shorter names: the nearer to just what was typed."""
        words = text.casefold().split()
        if not words:
            return []
        found = []
        for hit in self.jumping['index']:
            name, place = hit[1].casefold(), hit[2].casefold()
            inside = [w for w in words if w in name]
            if not inside or not all(w in name or w in place for w in words):
                continue
            begins = any(name.startswith(w) or ' ' + w in name for w in inside)
            found.append(((len(inside) < len(words), not begins, len(name)), hit))
            if len(found) >= most * 20:
                break
        found.sort(key=lambda f: f[0])
        return [hit for _, hit in found[:most]]

    def jump_rows(self, width, height):
        """The jump panel's lines: what matches, the chosen one lit, each with where it is."""
        P, j = self.paint, self.jumping
        quiet = P.fg('hint')
        if not j['text'].strip():
            return [quiet + 'type part of a name: a step, a person, a folder, a day',
                    quiet + f'looking through {len(j["index"]):,} entries: all that is open,',
                    quiet + 'and whatever is quick to look inside (tasks, the calendar)']
        if not j['hits']:
            return [quiet + 'nothing by that name here',
                    quiet + 'mail and files not opened yet are not looked through']
        room = max(1, height)
        first = max(0, min(j['sel'] - room // 2, len(j['hits']) - room))
        rows = []
        for i, (node, name, place) in enumerate(j['hits'][first:first + room], first):
            name = clip(name, max(8, width * 3 // 5))
            place = clip(place, max(0, width - cells(name) - 4))
            lit = i == j['sel']
            rows.append((P.on('bg', 'filter', True) + ' ' + name + ' \033[0m' if lit else
                         ' ' + P.fg('path', 1, False) + name + ' \033[0m') + quiet + ' ' + place)
        return rows

    # -- the keys there are
    def keys_rows(self):
        """Every key that does something here, for the F12 panel: this entry's own first."""
        P = self.paint
        acts = [] if isinstance(self.focus, Stub) else [h for _, _, h in self.source.actions(self.focus) if h]
        here = [tuple(h.split(' ', 1)) if ' ' in h else (h, '') for h in acts]
        groups = [
            ('On ' + clip(self.jump_name(self.focus), 30) if here else '', here),
            ('Moving', [('↑ ↓', 'between entries'), ('→ Tab', 'open a branch'), ('←', 'back to its parent'),
                        ('⏎', 'choose, mark or open it'), ('type', 'filter this column'),
                        ('⌫  ⌃U', 'a letter less, or the filter cleared'), ('⌃X', 'fold every branch but this one'),
                        ('PgUp PgDn', 'scroll the reading pane'), ('Esc', 'leave')]),
            ('Anywhere', [('⌃Space', 'jump to anything by name'), ('F1–F9', 'pin this in an empty slot, or go to that pin'),
                          ('⇧F1–F9', 'unpin (on or inside the pin), or move the pin here'), ('F10', 'clear every pin'),
                          ('⌃⇧C', 'copy this entry'), ('F12  ⌃/', 'these keys')]),
        ]
        wide = max(cells(key) for _, keys in groups for key, _ in keys)
        rows = []
        for title, keys in groups:
            if not keys:
                continue
            if rows:
                rows.append('')
            rows.append(P.fg('hint') + title)
            for key, what in keys:
                rows.append(' ' + P.fg('path', 1, True) + key + ' ' * (wide - cells(key)) + '\033[0m  ' +
                            P.fg('file') + what)
        return rows

    def panel_over(self, lines, w, title, rows, width=None, top=None, foot=2):
        """`rows` (styled lines) in a bordered panel over the canvas, whose own lines go dim behind
        it; the last `foot` (the bar and the hints) stay as they are."""
        P = self.paint
        plain = lambda s: PLACED.sub(' ', re.sub(r'\x1b\[[0-9;]*m', '', s))     # pictures go under it
        body = len(lines) - foot
        inner = min(w - 4, width or max([cells(plain(r)) for r in rows] + [cells(title) + 2]) + 2)
        if inner < 10 or body < 4:
            return lines
        rows = rows[:body - 2]
        x = max(0, (w - inner - 2) // 2)
        y = max(0, (body - len(rows) - 2) // 2) if top is None else min(top, max(0, body - len(rows) - 2))
        dim, border = P.fg('ghost'), P.fg('path')
        out = [dim + clip(plain(s), w) + '\033[0m' for s in lines[:body]] + list(lines[body:])
        heading = clip(' ' + title + ' ', inner)
        panel = [border + '╭' + heading + '─' * (inner - cells(heading)) + '╮']
        for r in rows:
            r = r if cells(plain(r)) <= inner - 1 else clip(plain(r), inner - 1)
            panel.append(border + '│\033[0m ' + r + '\033[0m' + ' ' * max(0, inner - 1 - cells(plain(r))) + border + '│')
        panel.append(border + '╰' + '─' * inner + '╯')
        for i, line in enumerate(panel):
            back = plain(lines[y + i])
            left, right, at = '', '', 0
            for ch in back:                 # what's behind, either side of the panel: by cells
                if at + cells(ch) <= x:
                    left += ch
                elif at >= x + inner + 2:
                    right += ch
                at += cells(ch)
            left += ' ' * (x - cells(left))
            out[y + i] = dim + left + '\033[0m' + line + '\033[0m' + dim + right + '\033[0m'
        return out

    def bookmark_hint(self):
        if not self.bookmarks:
            return 'F1–9 pin'
        free = next((i for i in range(1, 10) if i not in self.bookmarks), None)
        slots = '  '.join(f'F{i} {clip(self.bookmarks[i][1], 10)}' for i in sorted(self.bookmarks))
        return slots + (f'  F{free} pin' if free else '') + '  ⇧F unpin/move  F10 clear'

    def siblings(self):
        p = self.focus.parent
        return p.shown() if p else [self.focus]

    def move(self, step):
        p = self.focus.parent
        sib = self.siblings()
        if not p or not sib:
            return
        i = (sib.index(self.focus) + step) % len(sib) if self.focus in sib else 0
        protected = self.reference_paths(self.reference_nodes()) if self.bookmarks else set()
        if self.opts.fold and self.focus.open and id(self.focus) not in protected:
            self.focus.open = False
        p.sel, self.focus = i, sib[i]

    def enter(self):
        f = self.focus
        if not f.is_dir:
            return
        f.open = True
        kids = f.shown()
        # still coming (its children raised: 'fetching…'): go in once they're here, if you're still on it
        self.going_in = f if not kids and f.error else None
        if kids:
            if not f.entered:
                f.entered = True
                at = self.source.start_at(f)
                f.sel = len(kids) - 1 if at == -1 else at
            f.sel = max(0, min(f.sel, len(kids) - 1))
            self.focus = kids[f.sel]

    def leave(self):
        if self.focus.parent:
            self.focus = self.focus.parent

    def refilter(self, text):
        p = self.focus.parent
        if not p:
            return
        p.filter = text
        sib = p.shown()
        if self.focus in sib:
            p.sel = sib.index(self.focus)
        elif sib:
            p.sel, self.focus = 0, sib[0]
        # nothing matches: focus stays where it was, the column shows "(no match)"

    def fold(self):
        on = self.reference_paths(self.reference_nodes()) if self.bookmarks else set()
        n = self.focus
        while n:
            on.add(id(n)); n = n.parent
        stack = [self.root]
        while stack:
            n = stack.pop()
            if n.kids:
                stack.extend(n.kids)
            if id(n) not in on:
                n.open = False

    # -- colour of one entry
    def colour(self, n):
        """(colour, bold) for an entry at full strength."""
        if isinstance(n, Stub):
            return 'stub', False
        return self.source.colour(n) or (('dir' if n.is_dir else 'file'), False)

    # -- drawing
    def frame(self, w, h, animate=True):
        lines, moving = self.tree_frame(w, h, animate)
        if self.keys_shown and not self.layer:
            return self.panel_over(lines, w, 'Keys · any key closes', self.keys_rows()), moving
        if self.jumping is not None and not self.layer:
            P, j = self.paint, self.jumping
            found = f'{len(j["hits"])}{"+" if len(j["hits"]) >= 200 else ""} found' if j['text'].strip() else 'anything, by name'
            lines = self.panel_over(lines, w, 'Jump to · ' + found, self.jump_rows(min(w - 8, 96), max(3, h - 8)),
                                    width=min(w - 8, 96), top=1)
            lines[-2] = P.on('bg', 'filter', True) + ' jump to \033[0m ' + P.fg('path', 1, True) + \
                clip(printable(j['text']), max(4, w - 14)) + '▏\033[0m'
            lines[-1] = P.fg('hint') + clip('↑↓ choose  ⏎ go there  ⌃U clear  esc close', w - 1) + '\033[0m'
            return lines, moving
        if not self.layer:
            return lines, moving
        child, _, title = self.layer
        # Leave a rim of the original canvas visible beneath the file tree.
        margin_x, margin_y = (3 if w >= 40 else 0), (2 if h >= 14 else 0)
        width, height = max(8, w - margin_x * 2), max(5, h - margin_y * 2)
        inside, child_moving = child.frame(max(4, width - 2), max(3, height - 2), animate)
        dim = self.paint.fg('ghost')
        plain = lambda s: PLACED.sub(' ', re.sub(r'\x1b\[[0-9;]*m', '', s))     # pictures go under it
        base = [dim + clip(plain(s), w).ljust(w) + '\033[0m' for s in lines]
        border = self.paint.fg('path')
        heading = clip(' ' + title + ' · Esc back ', max(1, width - 2))
        panel = [border + '╭' + heading + '─' * max(0, width - cells(heading) - 2) + '╮\033[0m']
        for line in inside[:height - 2]:
            pad = max(0, width - 2 - cells(plain(line)))
            panel.append(border + '│\033[0m' + line + ' ' * pad + border + '│\033[0m')
        panel += [border + '╰' + '─' * max(0, width - 2) + '╯\033[0m']
        for i, line in enumerate(panel):
            y = margin_y + i
            if y >= len(base):
                break
            backdrop = plain(lines[y])
            left = clip(backdrop, margin_x).ljust(margin_x)
            # The rim is deliberately plain, never inherited terminal attributes.
            right = ' ' * max(0, w - margin_x - width)
            base[y] = dim + left + '\033[0m' + line + dim + right + '\033[0m'
        return base, moving or child_moving

    def tree_frame(self, w, h, animate=True):
        o, P, T = self.opts, self.paint, self.theme
        hard, soft, cap_l, cap_r = self.glyphs
        plain = o.glyphs == 'plain'
        self.take_signals()
        self.refresh()
        now, self.animate = self.clock(), animate
        moving_parts = self.in_motion(now)
        if self.focus is not self.told and not isinstance(self.focus, Stub):
            self.told = self.focus
            self.source.focus(self.focus)
        full = w            # the tree gets what the reading pane leaves
        references = self.reference_nodes()
        anchors = self.reference_paths(references) if self.bookmarks else None
        specs = self.pin_specs(references, w, h) if references else []
        # The pinboard has no reading pane: the pins are what's read, side by side in its width.
        shows = len(specs) < 2 and (isinstance(self.focus, Stub) or self.source.pane_for(self.focus))
        pane_w = int(w * o.pane_width) if self.pane and w >= 60 and shows else 0
        w -= pane_w
        reference_labels, reference_widths = {}, {}
        if specs:           # what each pin keeps in view stays open, on the tree or the pinboard
            context = {}
            for spec in specs:
                for n in [spec['root'], *spec['nodes']]:
                    context[id(n)] = n
                    if spec.get('width'):
                        reference_widths[id(n)] = spec['width']
                reference_labels.update(spec['labels'])
            anchors.update(self.reference_paths(context))
        if len(specs) < 2:          # one branch: the tree as it is, with the pins open on it
            specs = []
        for spec in specs:
            root = spec['root']
            where = self.crumbs(root, max(12, o.colmax - 8))     # room for '[F1] ' before it
            if where:               # its heading says where it is: what's above isn't drawn
                reference_labels[root.path] = where + '\n' + reference_labels.get(root.path, root.label())
        slots = {}
        for slot, n in references.items():
            slots.setdefault(id(n), []).append(str(slot))
        drawn, pictures = {}, {}     # a node's lines; (where its picture starts, its style)

        def lines_of(n):
            """A node's label as the lines it's drawn on: one, cut at colmax, or wrapped to its width."""
            got = drawn.get(id(n))
            if got is None:
                width = None if isinstance(n, Stub) else self.source.width(n)
                text = reference_labels.get(getattr(n, "path", None), n.label())
                if id(n) in slots:
                    text = '[F' + ',F'.join(slots[id(n)]) + '] ' + text
                width = reference_widths.get(id(n), width)
                got = wrap(text, width) if width else [clip(printable(l), o.colmax) for l in text.split('\n')]
                got = got or ['']
                path = None if GRAPHICS != 'kitty' or isinstance(n, Stub) else self.source.picture(n)
                pic = path and IMAGES.cells(path, width or o.colmax, self.source.picture_rows(n))
                if pic:         # under the label, in the picture's own colour (its id)
                    pictures[id(n)] = (len(got), pic[0])
                    got = got + pic[1]
                drawn[id(n)] = got
            return got
        layout(self.root, o, self.focus, lambda n: len(lines_of(n)), anchors)
        nodes = []
        place(self.root, 0, 0, self.focus, o, nodes)

        if specs:           # just what the pins keep, and '… N more' for each run they leave out
            for n in nodes:
                if isinstance(n, Stub) or not getattr(n, '_shown', None):
                    continue
                out, gap = [], 0
                for k in n.shown():
                    if id(k) in anchors:
                        if gap:
                            out.append(Stub(f'… {gap} more', n))
                        out.append(k)
                        gap = 0
                    else:
                        gap += 1
                if gap and out:
                    out.append(Stub(f'… {gap} more', n))
                n._shown = out
            nodes = []

            def walk(n, depth):
                n.depth = depth
                nodes.append(n)
                if is_open(n, self.focus, o.peek):
                    for k in getattr(n, '_shown', []):
                        walk(k, depth + 1)
            walk(self.root, 0)
        marks, chips = {}, {}   # (the mark beside it, how bright it is), and its chip colours, per entry
        wall, self.next_beacon, self.next_glow = self.wall(), None, None
        alert = (T.get('alert_fg', 15), T.get('alert_bg', T.get('git_conflict', 160)))
        for n in nodes:
            stub = isinstance(n, Stub)
            marks[id(n)] = (None, 1.0) if stub else (self.source.mark(n), self.source.dim(n))
            chips[id(n)] = None if stub else self.source.chip(n)
            came = None if stub else self.source.beacon(n)
            if came is not None:    # a beacon: a chip in the alert's colours when lit, its red when not
                lit, flips = blink(wall - came) if animate else (True, None)
                chips[id(n)] = alert if lit else (alert[1], 'bg')
                if flips is not None:
                    self.next_beacon = flips if self.next_beacon is None else min(self.next_beacon, flips)
        before = {id(n) for n in nodes if marks[id(n)][0] and self.source.mark_before(n)}
        leads = {n.depth: 2 for n in nodes if id(n) in before}    # room for a mark before, the column over
        counts = {id(n): self.source.count(n) for n in nodes if not isinstance(n, Stub)}
        counts = {k: v for k, v in counts.items() if v and v[0]}
        beside = {id(n) for n in nodes if id(n) in counts and self.source.count_beside(n)}
        names, numbers = {}, {}      # per column: how wide its names are, and its numbers
        pin_columns = {}             # on the pinboard, per branch: its widest counted name, and number
        for n in nodes:
            wd = max(cells(l) for l in lines_of(n)) + (2 if marks[id(n)][0] and id(n) not in before else 0) + \
                (2 if chips[id(n)] else 0) + leads.get(n.depth, 0) + \
                (cells(counts[id(n)][0]) + 2 if id(n) in beside else 0)
            names[n.depth] = max(names.get(n.depth, 0), wd)
            if id(n) in counts and id(n) not in beside:
                numbers[n.depth] = max(numbers.get(n.depth, 0), cells(counts[id(n)][0]))
        widths = {d: w + (numbers[d] + 2 if d in numbers else 0) for d, w in names.items()}
        extra = o.gap + (1 if cap_r else 0)
        xs = [0]
        for d in range(max(widths) + 1):
            xs.append(xs[-1] + widths.get(d, 0) + extra)

        positions = self.spread_references(nodes, specs, lines_of, w, h - 2) if specs else {}
        if specs:           # the pinboard: only the pins' branches
            nodes = [n for n in nodes if id(n) in positions]
        xpos = lambda n: positions.get(id(n), xs[n.depth])
        path = set()
        n = self.focus
        while n:
            path.add(id(n)); n = n.parent

        if anchors is not None:
            path.update(anchors)

        # Camera: keep the focus inside a box round the middle (a fraction of the view).
        view_h = h - 2 - (1 if o.header else 0)
        fx, fy = xpos(self.focus), self.focus.row
        aim_x, aim_y = w * o.across, view_h / 2
        if self.cam is None:
            self.cam = (fx - aim_x, fy - aim_y)
        tx, ty = self.target or self.cam
        half_w, half_h = w * o.dead / 2, view_h * o.dead / 2
        sx, sy = fx - tx, fy - ty
        if not aim_x - half_w <= sx <= aim_x + half_w:
            tx = fx - (aim_x - half_w if sx < aim_x - half_w else aim_x + half_w)
        if not aim_y - half_h <= sy <= aim_y + half_h:
            ty = fy - (aim_y - half_h if sy < aim_y - half_h else aim_y + half_h)
        if references:
            # Fit as many references as possible, retaining the current camera whenever it fits.
            # The active label always wins if the whole constellation is too large.
            shown_ids = {id(n) for n in nodes}
            active_width = max(cells(l) for l in lines_of(self.focus)) + 4
            left, right = fx, fx + min(active_width, max(1, w - 4))
            top, bottom = fy, fy + min(len(lines_of(self.focus)), max(1, view_h - 2))
            candidates = [n for n in nodes if id(n) in positions and positions[id(n)] >= 0] if specs else list(references.values())
            nearby = sorted(candidates, key=lambda n: abs(n.row - fy) + abs(xpos(n) - fx))
            for n in nearby:
                if id(n) not in shown_ids:
                    continue
                x, y = xpos(n), n.row
                bounds = (min(left, x), max(right, x + max(cells(l) for l in lines_of(n)) + 4),
                          min(top, y), max(bottom, y + len(lines_of(n))))
                if bounds[1] - bounds[0] <= w - 4 and bounds[3] - bounds[2] <= view_h - 2:
                    left, right, top, bottom = bounds
            tx, ty = self.target or self.cam
            tx = max(right - w + 2, min(tx, left - 2))
            ty = (top + bottom - view_h) / 2
            if specs:
                tx = max(right - w + 2, min(0, left - 2))
                board = (min(n.row for n in nodes), max(n.row + len(lines_of(n)) for n in nodes))
                if board[1] - board[0] <= view_h:       # the whole board fits: it holds still
                    ty = (board[0] + board[1] - view_h) / 2
        self.target = (tx, ty)
        cx, cy = self.cam
        if not animate or o.ease >= 1:
            cx, cy = tx, ty
        else:
            cx += (tx - cx) * o.ease
            cy += (ty - cy) * o.ease
            if abs(tx - cx) < 0.5 and abs(ty - cy) < 0.5:
                cx, cy = tx, ty
        self.cam = (cx, cy)
        ox, oy = round(cx), round(cy)

        grid = [[(' ', '')] * w for _ in range(view_h)]

        def put(x, y, text, style, behind=False):
            # One cell per column: a wide character fills two, the second holding '' (drawn as
            # nothing), so a row never runs past the screen's edge and wraps.
            y -= oy
            if not 0 <= y < view_h:
                return
            row, xx = grid[y], x - ox
            for ch in text:
                wide = cells(ch)
                if wide == 0:
                    if 0 < xx <= w and row[xx - 1][0]:
                        row[xx - 1] = (row[xx - 1][0] + ch, row[xx - 1][1])
                    continue
                if behind and 0 <= xx < w and row[xx] != (' ', ''):
                    xx += wide
                    continue
                for edge in range(xx, xx + wide):  # don't leave half of a wide character behind
                    if 0 <= edge < w:
                        if row[edge][0] == '' and edge > 0:
                            row[edge - 1] = (' ', row[edge - 1][1])
                        if edge + 1 < w and row[edge + 1][0] == '' and cells(row[edge][0][:1]) == 2:
                            row[edge + 1] = (' ', row[edge + 1][1])
                if 0 <= xx and xx + wide <= w:
                    row[xx] = (ch, style)
                    if wide == 2:
                        row[xx + 1] = ('', style)
                elif 0 <= xx < w:
                    row[xx] = (' ', style)
                xx += wide

        def dist(n):
            d = 0
            while id(n) not in path and n.parent is not None:
                n, d = n.parent, d + 1
            return d

        def strength(n):
            d = dist(n)
            if n.parent is self.focus.parent and n.parent is not None and d == 1:
                # the focus's own column: a wheel, fading with distance from the choice
                s = STRENGTH[min(abs(n.row - self.focus.row) // max(1, o.fade), len(STRENGTH) - 1)]
            else:
                s = STRENGTH[min(2 + d * 2, len(STRENGTH) - 1)]
            return max(s * marks[id(n)][1], 0.3)

        for n in nodes:
            x = xpos(n) + leads.get(n.depth, 0)
            lines = lines_of(n)
            text, widest = lines[0], max(cells(l) for l in lines)
            mark, chip = marks[id(n)][0], chips[id(n)]
            if n is self.focus or chip:      # a block: the choice in its colours, a chip in its own
                fg, bg = ('sel_fg', 'sel_bg') if n is self.focus else chip
                chip_rows = None if isinstance(n, Stub) else self.source.chip_rows(n)
                if id(n) in pictures:
                    chip_rows = min(chip_rows or pictures[id(n)][0], pictures[id(n)][0])
                chip_width = widest if chip_rows is None else max(cells(l) for l in lines[:chip_rows])
                for i, l in enumerate(lines):
                    if id(n) in pictures and i >= pictures[id(n)][0]:
                        put(x + 1, n.row + i, l, pictures[id(n)][1])
                    elif chip_rows is None or i < chip_rows:
                        put(x, n.row + i, ' ' + l + ' ' * (chip_width - cells(l) + 1), P.on(fg, bg, True))
                    else:
                        put(x + 1, n.row + i, l, P.fg('stub'))
                if cap_l:
                    put(x - 1, n.row, cap_l, P.fg(bg))
                cur = x + chip_width + 2
                if cap_r:
                    put(cur, n.row, cap_r, P.fg(bg))
                    cur += 1
                s = 1.0 if n is self.focus else strength(n)
            else:
                if id(n) in path:  # the way you came: its own colours with --grey-rest, else the path's
                    c, bold = self.colour(n) if o.grey_rest else ('path', True)
                    style, s = P.fg(c, 1, True), 1.0
                else:
                    s = strength(n)
                    c, bold = self.colour(n)
                    if o.grey_rest and n.parent is not self.focus.parent and n.parent is not self.focus:
                        c, bold = 'rest', False   # a branch you're not on
                    greyed = c == 'rest'
                    changed = None if isinstance(n, Stub) or greyed else self.source.glow(n)   # grey wins
                    if changed is not None:     # changed lately: towards the glow, by how lately
                        lit, until = glowing(wall - changed)
                        if lit:
                            c = blend(T.get(c, c) if isinstance(c, str) else c, T.get('glow', T.get('sel_bg', 214)), lit)
                        if until is not None:
                            due = changed + until - wall
                            self.next_glow = due if self.next_glow is None else min(self.next_glow, due)
                    style = P.fg(c, s, bold)
                if self.pulses and animate and not isinstance(n, Stub) and n.path in self.pulses:
                    t = (now - self.pulses[n.path]) / PULSE      # from the highlight back to itself
                    style = P.fg(blend(T.get('sel_bg', 214), T.get(c, c) if isinstance(c, str) else c, t),
                                 1 - (1 - s) * t, True)
                for i, l in enumerate(lines):
                    pic = pictures.get(id(n))
                    put(x + 1 if pic and i >= pic[0] else x, n.row + i, l, pic[1] if pic and i >= pic[0] else style)
                at, i = x, 0     # the label's start, over again in the provider's colours
                for count, c, bold in [] if isinstance(n, Stub) else self.source.tint(n):
                    part, i = text[i:i + count], i + count
                    put(at, n.row, part, P.fg(c, s, bold))
                    at += cells(part)
                cur = x + cells(text)
            if mark and id(n) in before:
                role, glyph, ascii_ = mark
                put(x - 2, n.row, ascii_ if plain else glyph, P.fg(role, s))
            elif mark:
                role, glyph, ascii_ = mark
                put(cur + 1, n.row, ascii_ if plain else glyph, P.fg(role, s))
                cur += 2
            if id(n) in beside:          # just after its own label
                text_n, role, bold = counts[id(n)]
                put(cur + 2, n.row, text_n, P.fg(role, s, bold))
                cur += 2 + cells(text_n)
            elif id(n) in counts:          # right-aligned in the column's own, after its names
                text_n, role, bold = counts[id(n)]
                if specs:       # the pinboard: a column of them per branch, after its widest name
                    if id(n.parent) not in pin_columns:
                        told = [k for k in getattr(n.parent, '_shown', [n]) if id(k) in counts and id(k) not in beside]
                        pin_columns[id(n.parent)] = (max(cells(l) for k in told for l in lines_of(k)),
                                                     max(cells(counts[id(k)][0]) for k in told))
                    wide, number = pin_columns[id(n.parent)]
                    right = xpos(n) + wide + 3 + number
                else:
                    right = xpos(n) + names[n.depth] + 2 + numbers[n.depth]
                put(right - cells(text_n), n.row, text_n, P.fg(role, s, bold))
                cur = right
            if is_open(n, self.focus, o.peek) and n._shown:
                kids = n._shown
                hot = id(n) in path and any(id(k) in path for k in kids)
                line = P.fg('line', STRENGTH[min(dist(n) * 2, len(STRENGTH) - 1)])
                lit = P.fg('path_line')
                spine = min(x - 2, min(xpos(k) for k in kids) - 2) if specs else min(xpos(k) for k in kids) - 2
                end = x - 1 if spine <= x else cur + 1
                put(min(end, spine), n.row, '─' * abs(spine - end), lit if hot else line, behind=bool(specs))
                top, bot = min(k.row for k in kids), max(k.row for k in kids)
                rows = {k.row for k in kids}
                strength_of_line = STRENGTH[min(dist(n) * 2, len(STRENGTH) - 1)]
                own = {k.row: P.fg(c, strength_of_line) for k in kids     # a child's own colour, into it
                       if not isinstance(k, Stub) and (c := self.source.line(k)) is not None}
                chosen = [k for k in kids if id(k) in path] if hot else []
                lo, hi = min(top, n.row), max(bot, n.row)      # the spine's ends: its corners
                for y in range(lo, hi + 1):
                    if y == n.row and specs and spine < x:      # the pinboard: hung under its parent, right of it
                        ch = '╭' if y == lo else '╰' if y == hi else '├'
                    elif y == n.row:
                        ch = ('─' if top == bot else '┬' if y == top else '┴' if y == bot else '┼') if y in rows else '┤'
                    elif y in rows:         # (the tree: its children's own ends, as ever)
                        ch = '╭' if y == (lo if specs else top) else '╰' if y == (hi if specs else bot) else '├'
                    else:
                        ch = '│'
                    # the lit part of the spine runs from the parent's row to the chosen child's
                    on = any(min(n.row, k.row) <= y <= max(n.row, k.row) for k in chosen)
                    put(spine, y, ch, lit if on else own.get(y, line), behind=bool(specs))
                for k in kids:
                    put(spine + 1, k.row, '─' * max(1, xpos(k) - spine - 1), lit if k in chosen else own.get(k.row, line), behind=bool(specs))

        self.backdrop_under(grid, 1 if o.header else 0)
        lines = [o.header(full)] if o.header else []
        side = self.pane_rows(pane_w, view_h) if pane_w else [''] * view_h
        for row, beside in zip(grid, side):
            s, cur = [], None
            for ch, st in row:
                if st != cur:
                    s.append('\033[0m' + st); cur = st
                s.append(ch)
            lines.append(''.join(s) + '\033[0m' + beside)
        lines.append(self.typing_line(full) if self.asking else self.bar(full))
        if o.footer and not self.asking:
            lines.append(o.footer(full))
        elif self.asking:
            hint = 'esc cancel' if self.asking[3] is not None else '⏎ done  esc cancel  ' + (
                '⌃V paste a picture or files  ' if self.attaching else '') + '←→ ⌃←→ move  ⌃W word  ⌃U ⌃K clear'
            lines.append(P.fg('hint') + hint[:full - 1] + '\033[0m')
        else:
            acts = [h for _, _, h in self.source.actions(self.focus) if h] if not isinstance(self.focus, Stub) else []
            hint = '  '.join(acts + ['F12 keys  ⌃Space jump', self.bookmark_hint()] + ['↑↓ move  →/tab open  ← parent  type filter  ⌃X fold  ' +
                                     ('pgup/pgdn scroll  ' if pane_w else '') + '⏎ choose  esc cancel'])
            lines.append(P.fg('hint') + clip(hint, full - 1) + '\033[0m')
        # motion begun while drawing (a pane's reveal) counts too: the next frame must follow
        self.flowing = animate and ((ox, oy) != (round(tx), round(ty)) or bool(self.pulses or self.reveal) or
                                    any(v[1] == 'gone' for v in self.statuses.values()))
        return lines, (ox, oy) != (round(tx), round(ty)) or self.source.busy() or moving_parts or \
            bool(self.reveal)

    def pane_lines(self, preview, room, height):
        """The pane's lines, styled, for what preview() gave: text, a Picture, or a list of them."""
        out = []
        for part in preview if isinstance(preview, list) else [preview]:
            if isinstance(part, Picture):
                out += part.lines(room, max(4, min(height - 1, part.rows)), self.opts.truecolor)
            elif isinstance(part, Chips):
                width = sum(cells(t) + 3 for t, *_ in part.segs)    # a chip: ' text ' and its separator
                segs = [(clip(printable(t), max(1, room - 4)), *rest) for t, *rest in part.segs]
                after = clip(printable(part.after), max(0, room - width))
                out.append(self.chips(segs) + self.paint.fg('hint') + after)
            elif part is not None:
                out += [self.paint.fg('file') + line for line in wrap(str(part), room)]
        return out

    def typing_line(self, w):
        """The open typing line: its prompt in a chip, then what's typed, the end of it if it's long."""
        prompt, text, _, choices, _ = self.asking
        P = self.paint
        head = P.on('bg', 'filter', True) + ' ' + printable(prompt) + ' ' + '\033[0m '
        room = max(4, w - cells(prompt) - 5)
        at = max(0, min(self.cursor, len(text)))
        before, after = printable(text[:at]), printable(text[at:])
        while cells(before) > room - 1:             # long: the part around the cursor
            before = before[1:]
        while cells(before) + 1 + cells(after) > room:
            after = after[:-1]
        return head + ('' if choices is not None else P.fg('path', 1, True) + before + '▏' + after) + '\033[0m'

    def pane_rows(self, width, height):
        """The reading pane, `height` rows `width` wide: a line down its left side, the choice's
        name, and its preview wrapped, from self.scroll down."""
        P, f = self.paint, self.focus
        end = self.source.preview_end(f)
        before = self._preview[2] if self._preview[0] is f else None
        following = end and self._at_end
        if self._preview[0] is not f:
            self.scroll = sys.maxsize if end else 0
        elif end and self._preview[1] == -1 and self._at_end:
            self.scroll = sys.maxsize     # fetched again (refresh) with you at the end: stay there
        if self._preview[0] is not f or self._preview[1] != width:
            text = self.source.preview(f)
            self._preview = (f, width, None if text is None else self.pane_lines(text, width - 3, height - 2))
            grew = before is not None and self._preview[2] is not None and len(self._preview[2]) > len(before)
            if grew and following and self.animate:      # what's new at the end resolves out of noise
                self.reveal = (f, len(before), self.clock())
        body = self._preview[2]
        room = width - 3
        self.page = max(1, height - 2)
        self._at_end = body is not None and self.scroll >= len(body) - self.page
        rows = [P.fg('path', 1, True) + clip(printable(f.label()), room)]
        if body is not None:
            self.scroll = max(0, min(self.scroll, len(body) - self.page))
            rule = '─' * room
            if len(body) > self.page:   # where in it you are
                at = f' {self.scroll + 1}–{min(self.scroll + self.page, len(body))} of {len(body)} '
                rule = clip('─' * max(0, room - cells(at) - 2) + at + '──', room)
            rows.append(P.fg('line', STRENGTH[5]) + rule)
            shown = body[self.scroll:self.scroll + self.page]
            if self.reveal and self.reveal[0] is f and self.animate:
                left = 1 - (self.clock() - self.reveal[2]) / REVEAL
                shown = [scramble(l, left, self.scroll + i) if self.scroll + i >= self.reveal[1] else l
                         for i, l in enumerate(shown)]
            rows += shown
        edge = P.fg('line', STRENGTH[4]) + '│ '
        return ['\033[0m' + edge + (rows[i] if i < len(rows) else '') + '\033[0m' for i in range(height)]

    def backdrop_under(self, grid, top):
        """The screen behind (self.backdrop, {(row, col): char}), dimmed, in the grid's empty cells
        that have an empty cell either side, so what's drawn stays clear of it."""
        if not self.backdrop:
            return
        ghost = self.paint.fg('ghost')
        w = len(grid[0]) if grid else 0

        drawn = {(y, x) for y, row in enumerate(grid) for x, cell in enumerate(row) if cell != (' ', '')}
        for (y, x), ch in self.backdrop.items():
            y -= top
            if not 0 <= y < len(grid):
                continue
            row, wide = grid[y], cells(ch) == 2
            if not any((y, c) in drawn for c in range(x - 1, x + (3 if wide else 2))) and 0 <= x and x + wide < w:
                row[x] = (ch, ghost)
                if wide:
                    row[x + 1] = ('', ghost)

    def bar(self, w):
        """The way to the choice as powerline-shell draws a prompt (the provider's segments), then
        its tail and the filter."""
        P = self.paint
        hard, soft, _, _ = self.glyphs
        segs, tail = self.source.bar(self.focus, self.opts.glyphs == 'plain')   # (text, fg, bg, bold)
        segs, tail = list(segs), list(tail)
        p = self.focus.parent
        if p and p.filter:
            tail.append(('filter: ' + p.filter + '▏', 'bg', 'filter', False))
        if self.message:
            tail.append((self.message, 'bg', 'filter', False))
        now = self.clock()
        for key, (text, state, since) in self.statuses.items():
            if state == 'busy':
                glyph = SPIN[int((now - since) * 12) % len(SPIN)] if self.animate else '…'
                tail.append((glyph + ' ' + text, 'bg', 'filter', False))
            elif state == 'ok':
                tail.append(('✓ ' + text, 'repo_clean_fg', 'repo_clean_bg', False))
            elif state == 'failed':
                tail.append(('✗ ' + text, 'repo_dirty_fg', 'repo_dirty_bg', True))
            elif state == 'note':
                tail.append((text, 'bg', 'filter', False))
            elif state == 'gone' and self.animate:
                tail.append((dust(text, (now - since) / DISSOLVE, hash(key) % 97), 'bg', 'filter', False))

        segs = [(printable(t), *rest) for t, *rest in segs]
        tail = [(printable(t), *rest) for t, *rest in tail]

        def width(ss):
            return sum(cells(t) + 3 for t, *_ in ss)
        # Too long: the first folders after ~ give way to one '…', the last one always stays.
        while width(segs + tail) > w - 1 and len(segs) > 2:
            if segs[1][0] != '…':
                segs[1] = ('…', 'seg_fg', 'seg_bg', False)
            elif len(segs) > 3:
                del segs[2]
            else:
                break
        segs += tail
        while width(segs) > w - 1 and len(segs) > 1:   # still too long: drop from the end
            segs.pop()
        if width(segs) > w - 1:
            segs[0] = (clip(segs[0][0], max(1, w - 4)), *segs[0][1:])
        return self.chips(segs)

    def chips(self, segs):
        """(text, fg, bg, bold) segments drawn as the bar draws them: chips, joined by the
        glyphs' separators."""
        P = self.paint
        hard, soft, _, _ = self.glyphs
        out = []
        for i, (text, fg, bg, bold) in enumerate(segs):
            out.append(P.on(fg, bg, bold) + ' ' + text + ' ')
            nxt = segs[i + 1] if i + 1 < len(segs) else None
            if nxt and nxt[2] == bg:
                out.append(P.on('sep_fg', bg) + (soft or ' '))
            elif nxt:
                out.append(P.on(bg, nxt[2]) + hard if hard else '')
            else:
                out.append('\033[0m' + P.fg(bg) + hard if hard else '')
        return ''.join(out) + '\033[0m'


def display(path):
    cwd = os.getcwd()
    if path == cwd:
        return '.'
    if path.startswith(cwd.rstrip('/') + '/'):
        return os.path.relpath(path, cwd)
    home = os.path.expanduser('~')
    if path == home or path.startswith(home + '/'):
        return '~' + path[len(home):]
    return path


def shell_word(path, fish=False):
    """`path` for a shell command line: as display() writes it, quoted, a leading ~/ left to expand.
    Fish's single quotes take \\ and \\' as escapes, so it gets its own quoting."""
    import re, shlex

    def quote(text):
        if fish:
            return text if re.fullmatch(r'[\w@%+=:,./-]+', text) else \
                "'" + text.replace('\\', '\\\\').replace("'", "\\'") + "'"
        return shlex.quote(text)
    shown = display(path)
    if shown == '~':
        return '~'
    if shown.startswith('~/'):
        return '~/' + (quote(shown[2:]) if shown[2:] else '')
    return quote(shown)


INIT = {
    'bash': '''# treecanvas: `@CMD@ [FOLDER]` goes to a folder you choose on the canvas;@KEYDOC@
if ! type -t @CMD@ >/dev/null 2>&1 || [[ $(declare -f @CMD@ 2>/dev/null) == *treecanvas* || $(declare -f @CMD@ 2>/dev/null) == *filecanvas* ]]; then
    @CMD@() { local p; p=$(@RUN@ --dirs-only --on-start --print absolute "${1:-.}") && builtin cd -- "$p"; : treecanvas; }
else
    echo "treecanvas: @CMD@ is already a command here, so it's left alone (--cmd NAME picks another)" >&2
fi
_treecanvas_insert() {
    local p; p=$(@RUN@ --print shell) || return
    READLINE_LINE=${READLINE_LINE:0:READLINE_POINT}$p${READLINE_LINE:READLINE_POINT}
    READLINE_POINT=$((READLINE_POINT + ${#p}))
}
@BIND@''',
    'fish': '''# treecanvas: `@CMD@ [FOLDER]` goes to a folder you choose on the canvas;@KEYDOC@
if not type -q @CMD@; or functions @CMD@ 2>/dev/null | string match -qr 'treecanvas|filecanvas'
    function @CMD@ --description 'cd to a folder chosen on the canvas (treecanvas)'
        set -l start .
        set -q argv[1]; and set start $argv[1]
        set -l p (@RUN@ --dirs-only --on-start --print absolute $start | string collect); and builtin cd -- $p
    end
else
    echo "treecanvas: @CMD@ is already a command here, so it's left alone (--cmd NAME picks another)" >&2
end
function _treecanvas_insert --description 'put a path chosen on the canvas in at the cursor (treecanvas)'
    set -l p (@RUN@ --print fish | string collect); and commandline -i -- $p
    commandline -f repaint
end
@BIND@''',
    'zsh': '''# treecanvas: `@CMD@ [FOLDER]` goes to a folder you choose on the canvas;@KEYDOC@
if (( ! $+commands[@CMD@] && ! $+aliases[@CMD@] && ! $+builtins[@CMD@] )) && [[ -z ${functions[@CMD@]} || ${functions[@CMD@]} == *(treecanvas|filecanvas)* ]]; then
    @CMD@() { local p; p=$(@RUN@ --dirs-only --on-start --print absolute "${1:-.}") && builtin cd -- "$p"; : treecanvas }
else
    print -u2 "treecanvas: @CMD@ is already a command here, so it's left alone (--cmd NAME picks another)"
fi
_treecanvas_insert() { local p; p=$(@RUN@ --print shell) && LBUFFER+=$p; zle reset-prompt }
zle -N _treecanvas_insert
@BIND@''',
}


def init(shell, cmd='cdi', key='ctrl-t', run=None):
    """The lines for a shell's rc file: `cmd` and, unless key is 'none', a key that inserts a path."""
    import re, shlex
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', cmd):
        sys.exit(f'treecanvas: {cmd!r} is not a command name')
    run = run or shlex.join([sys.executable, os.path.abspath(__file__)])
    bind, doc = '', ''
    m = re.fullmatch(r'(ctrl|alt)-([a-z])', key.lower())
    if m:
        mod, letter = m.groups()
        doc = f' {key} puts a path in at the cursor.'
        if shell == 'bash':
            seq = ('\\C-' if mod == 'ctrl' else '\\e') + letter
            bind = "bind -x '\"" + seq + "\": _treecanvas_insert'"
        elif shell == 'fish':
            seq = ('\\c' if mod == 'ctrl' else '\\e') + letter   # in vi mode too
            bind = f"bind {seq} _treecanvas_insert\nbind -M insert {seq} _treecanvas_insert"
        else:
            seq = '^' + letter.upper() if mod == 'ctrl' else '^[' + letter
            bind = "bindkey '" + seq + "' _treecanvas_insert"
    elif key != 'none':
        sys.exit(f"treecanvas: --key takes ctrl-<letter>, alt-<letter> or none, not {key!r}")
    return (INIT[shell].replace('@CMD@', cmd).replace('@RUN@', run).replace('@BIND@', bind)
            .replace('@KEYDOC@', doc).rstrip() + '\n')


CSI = re.compile(r'\x1b\[([0-9;]*)([A-Za-z~])|\x1bO([A-Za-z])')
ARROWS = {'A': 'up', 'B': 'down', 'C': 'right', 'D': 'left', 'H': 'home', 'F': 'end'}
FUNCTIONS = {'P': 'f1', 'Q': 'f2', 'R': 'f3', 'S': 'f4'}
TILDES = {'11': 'f1', '12': 'f2', '13': 'f3', '14': 'f4', '15': 'f5', '17': 'f6', '18': 'f7', '19': 'f8', '20': 'f9', '21': 'f10', '23': 'f11', '24': 'f12', '1': 'home', '7': 'home', '4': 'end', '8': 'end', '3': 'delete', '2': 'insert', '5': 'pgup', '6': 'pgdn'}
MODS = {'2': 'shift-', '3': 'alt-', '4': 'alt-shift-', '5': 'ctrl-', '6': 'ctrl-shift-', '7': 'ctrl-alt-', '8': 'ctrl-alt-shift-'}


PASTE_START, PASTE_END = '\x1b[200~', '\x1b[201~'


class Paste(str):
    """What was pasted, as one key: the typing line takes it whole (files in it attached, where
    they can be); elsewhere it's the keys it is."""


def pasted_files(text):
    """The files `text` names, when it names nothing else: paths or file:// URIs, one a line or
    shell-quoted with spaces between (as file managers copy them and terminals drop them).
    [] when it's anything but existing files."""
    import shlex, urllib.parse
    text = text.strip().replace('\r', '')
    if not text:
        return []
    lines = [l for l in text.split('\n') if l.strip() and l.strip() not in ('copy', 'cut')]
    for parts in (lines, None):
        if parts is None:
            try:
                parts = shlex.split(text)
            except ValueError:
                return []
        out = []
        for part in parts:
            part = part.strip()
            if part.startswith('file://'):
                part = urllib.parse.unquote(urllib.parse.urlparse(part).path)
            part = os.path.expanduser(part)
            if not os.path.isabs(part) or not os.path.isfile(part):
                break
            out.append(part)
        else:
            if out:
                return out
    return []


def clipboard():
    """What the clipboard holds that could be sent: ('files', [paths]) for files copied or a
    picture (saved as a file), ('text', text), or None. Wayland's wl-paste, X's xclip, macOS's
    pngpaste and pbpaste: whichever is here."""
    def run(cmd, binary=False):
        try:
            got = subprocess.run(cmd, capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        return (got.stdout if binary else got.stdout.decode(errors='replace')) if got.returncode == 0 else None
    where = os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'twigs', 'pasted')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    if sys.platform == 'darwin':
        if shutil.which('pngpaste'):
            os.makedirs(where, exist_ok=True)
            path = os.path.join(where, f'pasted-{stamp}.png')
            if run(['pngpaste', path]) is not None and os.path.isfile(path):
                return 'files', [path]
        text = run(['pbpaste']) if shutil.which('pbpaste') else None
        return ('files', pasted_files(text)) if text and pasted_files(text) else ('text', text) if text else None
    if os.environ.get('WAYLAND_DISPLAY') and shutil.which('wl-paste'):
        types = lambda: (run(['wl-paste', '--list-types']) or '').split()
        read = lambda t, binary=False: run(['wl-paste', '--no-newline', '--type', t], binary)
    elif os.environ.get('DISPLAY') and shutil.which('xclip'):
        types = lambda: (run(['xclip', '-selection', 'clipboard', '-t', 'TARGETS', '-o']) or '').split()
        read = lambda t, binary=False: run(['xclip', '-selection', 'clipboard', '-t', t, '-o'], binary)
    else:
        return None
    have = types()
    for kind in ('text/uri-list', 'x-special/gnome-copied-files'):
        if kind in have:
            got = pasted_files(read(kind) or '')
            if got:
                return 'files', got
    pictures = [t for t in have if t.startswith('image/')]
    if pictures:
        kind = 'image/png' if 'image/png' in pictures else pictures[0]
        data = read(kind, binary=True)
        if data:
            os.makedirs(where, exist_ok=True)
            ext = {'image/jpeg': 'jpg', 'image/svg+xml': 'svg'}.get(kind, kind.split('/')[1].split(';')[0])
            path = os.path.join(where, f'pasted-{stamp}.{ext}')
            with open(path, 'wb') as f:
                f.write(data)
            return 'files', [path]
    for kind in ('UTF8_STRING', 'text/plain;charset=utf-8', 'text/plain', 'STRING'):
        if kind in have:
            text = read(kind)
            if text:
                got = pasted_files(text)
                return ('files', got) if got else ('text', text)
    return None


def copy(text):
    """Put `text` on the clipboard: wl-copy, xclip or pbcopy where there's one, else the
    terminal's own (OSC 52, which kitty and most others take). True when it went somewhere."""
    for cmd, needs in ((['wl-copy'], 'WAYLAND_DISPLAY'), (['xclip', '-selection', 'clipboard'], 'DISPLAY'),
                       (['pbcopy'], None)):
        if shutil.which(cmd[0]) and (needs is None and sys.platform == 'darwin' or needs and os.environ.get(needs)):
            try:    # xclip stays behind to hold the selection: it mustn't hold our pipes too
                subprocess.run(cmd, input=text.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=5, check=True)
                return True
            except (OSError, subprocess.SubprocessError):
                pass
    try:
        import base64
        with open('/dev/tty', 'wb', buffering=0) as t:
            t.write(b'\033]52;c;' + base64.b64encode(text.encode()) + b'\a')
        return True
    except OSError:
        return False


def copy_focus(c):
    """Ctrl+Shift+C: what the provider says the entry under the cursor holds, to the clipboard."""
    text = None if isinstance(c.focus, Stub) else c.source.copy_text(c.focus)
    if not text:
        c.say('nothing here to copy')
    elif copy(text):
        flat = ' '.join(text.split())
        c.say('copied: ' + (flat if len(flat) <= 50 else flat[:49] + '…'))
    else:
        c.say('no clipboard to copy to')


# kitty passes Ctrl+Shift+C (its own copy) through only to a window holding this user variable:
# see docs/core.md. Set while a canvas is on the screen; elsewhere Ctrl+Shift+C stays kitty's.
HOLDING, LETTING_GO = '\033]1337;SetUserVar=twigs=MQ==\a', '\033]1337;SetUserVar=twigs\a'


def keys(data):
    """The keys in what the terminal sent: characters, and named keys for its sequences ('left',
    'ctrl-left', 'alt-up', 'home', 'delete', 'pgdn', 'backtab'…). A sequence it doesn't know is
    left out whole, never taken for Esc (Ctrl+arrows, say, mustn't close a canvas); 'esc' is a
    lone Esc."""
    i = 0
    while i < len(data):
        if data[i] != '\x1b':
            yield data[i]
            i += 1
            continue
        if data.startswith(PASTE_START, i):     # pasted, whole (the terminal's bracketed paste)
            end = data.find(PASTE_END, i)
            end = len(data) if end < 0 else end
            yield Paste(data[i + len(PASTE_START):end])
            i = end + len(PASTE_END)
            continue
        m = CSI.match(data, i)
        if not m:
            yield 'esc'
            i += 1
            continue
        i = m.end()
        params, final, ss3 = m.group(1) or '', m.group(2), m.group(3)
        if ss3:
            if ss3 in ARROWS:
                yield ARROWS[ss3]
            elif ss3 in FUNCTIONS:
                yield FUNCTIONS[ss3]
            continue
        parts = params.split(';')
        mod = MODS.get(parts[1], '') if len(parts) > 1 else ''
        if final in FUNCTIONS and parts[0] in ('', '1'):
            yield mod + FUNCTIONS[final]
        elif final in ARROWS:
            yield mod + ARROWS[final]
        elif final == 'Z':
            yield 'backtab'
        elif final == 'u' and parts[0].isdigit():      # CSI u, a key as kitty's protocol sends it
            yield mod + chr(int(parts[0])).lower()
        elif final == '~' and parts[0] in TILDES:
            yield mod + TILDES[parts[0]]


def apply(c, k, opts):
    """One key; returns ('done', what the provider chose: the path) / ('cancel', None) / None."""
    if c.layer:
        child, done, _ = c.layer
        result = apply(child, k, child.opts)
        if result:
            if result[0] == 'done' and os.path.isdir(result[1]):
                child.enter()
                return None
            c.layer = None
            if result[0] == 'done':
                done(result[1])
        return None
    if isinstance(k, Paste):
        if c.asking:
            return answer(c, k)
        for ch in k:                    # not typing: the keys it is
            r = apply(c, ch, opts)
            if r:
                return r
        return None
    if c.asking:
        return answer(c, k)
    c.message = None
    p = c.focus.parent
    if c.keys_shown:                    # the panel of keys: any key puts it away
        c.keys_shown = False
        return None
    if c.jumping is not None:
        return jump_key(c, k)
    if k in ('esc', '\x03', '\x07'):
        return 'cancel', None
    if k == 'ctrl-shift-c':
        copy_focus(c)
        return None
    if k in ('f12', '\x1f'):            # ⌃/ too: F12 is some terminals' own
        c.keys_shown = True
        return None
    if k in ('\x00', 'ctrl- '):
        c.jump_open()
        return None
    acts = {} if isinstance(c.focus, Stub) else {key: name for key, name, _ in c.source.actions(c.focus)}
    if k in acts:
        done = c.source.act(c.focus, acts[k], c)
        return None if done is None else ('done', done)
    if re.fullmatch(r'(shift-)?f[1-9]', k):
        c.bookmark(int(k[-1]), replace=k.startswith('shift-'))
        return None
    if k in ('f10', '\x02'):
        c.bookmarks.clear()
        c.save_pins()
        c.say('pins cleared')
        return None
    if k in ('\r', '\n'):
        chosen = c.source.choose(c.focus)
        if chosen is STAY:
            return None
        if chosen is not None:
            return 'done', chosen
        c.enter()       # staying on the canvas: a branch opens
        return None
    if k in ('pgup', 'pgdn'):
        c.scroll = max(0, c.scroll + (c.page if k == 'pgdn' else -c.page))
        return None
    if k == 'up' or (k == 'backtab' and opts.tab == 'cycle'):
        c.move(-1)
    elif k == 'down' or (k == '\t' and opts.tab == 'cycle'):
        c.move(1)
    elif k in ('right', '\t'):
        c.enter()
    elif k == 'left':
        c.leave()
    elif k == '\x18':
        c.fold()
    elif k in ('\x7f', '\x08'):
        if p and p.filter:
            c.refilter(p.filter[:-1])
    elif k == '\x15':
        c.refilter('')
    elif len(k) == 1 and k.isprintable():
        c.refilter((p.filter if p else '') + k)
    return None


def jump_key(c, k):
    """A key while the jump panel is open: typed into what's looked for; ↑ ↓ among what matches,
    Enter goes there, Esc puts it away."""
    j = c.jumping
    if k in ('esc', '\x03', '\x07', '\x00', 'ctrl- '):
        c.jumping = None
    elif k in ('\r', '\n'):
        c.jumping = None
        if j['hits']:
            node = j['hits'][min(j['sel'], len(j['hits']) - 1)][0]
            if c.go(c.chain(node)):
                c.scroll = 0
                c.source.pulse(node.path)
            else:
                c.say('it has gone since')
    elif k in ('up', 'down', 'backtab', '\t'):
        if j['hits']:
            j['sel'] = (j['sel'] + (1 if k in ('down', '\t') else -1)) % len(j['hits'])
    elif k in ('pgup', 'pgdn'):
        if j['hits']:
            j['sel'] = max(0, min(len(j['hits']) - 1, j['sel'] + (8 if k == 'pgdn' else -8)))
    else:
        text = j['text']
        if k in ('\x7f', '\x08'):
            text = text[:-1]
        elif k == '\x15':
            text = ''
        elif k == '\x17':
            text = text[:word_left(text, len(text))]
        elif len(k) == 1 and k.isprintable():
            text += k
        if text != j['text']:
            j['text'], j['sel'], j['hits'] = text, 0, c.jump_hits(text)
    return None


def word_left(text, at):
    while at > 0 and text[at - 1] == ' ':
        at -= 1
    while at > 0 and text[at - 1] != ' ':
        at -= 1
    return at


def word_right(text, at):
    while at < len(text) and text[at] == ' ':
        at += 1
    while at < len(text) and text[at] != ' ':
        at += 1
    return at


def answer(c, k):
    """A key while the typing line is open: typed at the cursor, which moves by character (← →),
    by word (Ctrl+← →, Alt+← →), to either end (Home End, ⌃A ⌃E); Backspace and Delete either
    side of it, ⌃W the word before it, ⌃U all before it, ⌃K all after."""
    prompt, text, done, choices, changed = c.asking
    if isinstance(k, Paste) or (k == '\x16' and choices is None):
        got = ('text', str(k)) if isinstance(k, Paste) else clipboard() if c.attaching else None
        if got and got[0] == 'text' and c.attaching and pasted_files(got[1]):
            got = ('files', pasted_files(got[1]))
        if got and got[0] == 'files':
            if c.attaching:
                c.attaching(got[1], text)
                return None
            got = ('text', ' '.join(got[1]))
        if choices is not None or not got:
            if k == '\x16':
                c.message = 'nothing in the clipboard to send'
            return None
        k = ' '.join(got[1].replace('\r', '').split('\n'))    # one line: what was on many
        at = max(0, min(c.cursor, len(text)))
        text = text[:at] + ''.join(ch for ch in k if ch.isprintable()) + text[at:]
        c.cursor = at + len(text) - len(c.asking[1])
        if changed:
            changed(text)
        c.asking = (prompt, text, done, choices, changed)
        return None
    if k in ('esc', '\x03', '\x07'):
        c.asking = None
        if changed:
            changed(None)
        return None
    if choices is not None:
        if len(k) != 1 or k.lower() not in choices:
            return None
        c.asking, text = None, k.lower()
    elif k in ('\r', '\n'):
        c.asking = None
    else:
        at = max(0, min(c.cursor, len(text)))
        if k in ('left', 'right'):
            at = max(0, at - 1) if k == 'left' else min(len(text), at + 1)
        elif k in ('ctrl-left', 'alt-left'):
            at = word_left(text, at)
        elif k in ('ctrl-right', 'alt-right'):
            at = word_right(text, at)
        elif k in ('home', '\x01'):
            at = 0
        elif k in ('end', '\x05'):
            at = len(text)
        elif k in ('\x7f', '\x08'):
            text, at = text[:max(0, at - 1)] + text[at:], max(0, at - 1)
        elif k == 'delete':
            text = text[:at] + text[at + 1:]
        elif k == '\x15':                 # ⌃U: all before the cursor
            text, at = text[at:], 0
        elif k == '\x0b':                 # ⌃K: all after it
            text = text[:at]
        elif k == '\x17':                 # ⌃W: the word before it
            start = word_left(text, at)
            text, at = text[:start] + text[at:], start
        elif len(k) == 1 and k.isprintable():
            text, at = text[:at] + k + text[at:], at + 1
        c.cursor = at
        if changed and text != c.asking[1]:
            changed(text)
        c.asking = (prompt, text, done, choices, changed)
        return None
    if changed:
        changed(None)
    r = done(text)
    return None if r is None else ('done', r)


def terminal(cmd, **kw):
    """Run `cmd` on the terminal itself (an editor, a pager), even when stdout is a pipe, as it
    is under cdi; its exit status. For outside()."""
    with open('/dev/tty', 'r+b', buffering=0) as t:
        return subprocess.call(cmd, stdin=t, stdout=t, stderr=t, **kw)


def capture():
    """{(row, col): char} of the tmux pane we're in, as it is now; {} outside tmux."""
    pane = os.environ.get('TMUX_PANE')
    if not (pane and os.environ.get('TMUX')):
        return {}
    try:
        text = subprocess.run(['tmux', 'capture-pane', '-p', '-t', pane], capture_output=True,
                              text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    out = {}
    for y, line in enumerate(text.split('\n')):
        x = 0
        for ch in line:
            if not ch.isspace() and ch.isprintable():
                out[(y, x)] = ch
            x += cells(ch)
    return out


def label_rows(source, node, text, width, colmax=24):
    """How many rows `node` takes drawn with `text`, wrapped to `width` (None: a row a line), and
    its picture under it where the terminal draws pictures: as the pinboard will draw it, for a
    provider fitting a pin to its room."""
    rows = len(wrap(text, width)) if width else len(text.split('\n'))
    path = source.picture(node) if GRAPHICS == 'kitty' else None
    pic = path and IMAGES.cells(path, width or colmax, source.picture_rows(node))
    return rows + (len(pic[1]) if pic else 0)


def pins_path(source):
    """Where a canvas showing `source` keeps its pins: one file per kind of tree (twigs, files, mail)."""
    kind = type(source).__module__ + '.' + type(source).__qualname__
    sections = getattr(source, 'sections', None)        # twigs alone and `twigs msg`: apart
    if isinstance(sections, list) and all(isinstance(s, tuple) and s and isinstance(s[0], str) for s in sections):
        kind += '-' + '+'.join(name for name, _ in sections)
    return os.path.join(os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state'),
                        'twigs', 'pins', re.sub(r'[^A-Za-z0-9_.-]', '_', kind) + '.json')


def earlier_pins(path):
    """The pins kept before this canvas gained a section: the newest file of the same kind whose
    sections are all still here (so a new twig in `twigs` doesn't lose the pins; `twigs msg`
    alone doesn't take twigs' own). `path` itself when there's none."""
    folder, name = os.path.split(path)
    kind, dash, mine = name[:-len('.json')].partition('-')
    if not dash:
        return path
    mine, found = set(mine.split('_')), []
    try:
        for other in os.listdir(folder):
            k, d, theirs = other[:-len('.json')].partition('-')
            if other.endswith('.json') and other != name and k == kind and d and set(theirs.split('_')) < mine:
                found.append(os.path.join(folder, other))
        return max(found, key=os.path.getmtime) if found else path
    except OSError:
        return path


def run(opts):
    """Draw the canvas on the terminal until Enter (the chosen path, absolute) or Esc (None)."""
    if opts.backdrop == 'auto':
        opts.backdrop = capture()
    c = Canvas(opts.start, opts)
    if globals()['_RUNS'] == 0:     # the canvas you opened keeps its pins; a layer over it doesn't
        c.load_pins(pins_path(c.source))
    tty = open('/dev/tty', 'r+b', buffering=0)
    fd = tty.fileno()
    import termios, tty as ttymod
    old = termios.tcgetattr(fd)

    def out(s):
        data = memoryview(s.encode())
        while data:         # all of it: a picture is more than the terminal takes in one write
            try:
                n = tty.write(data)
            except BlockingIOError:
                n = 0
            if not n:
                select.select([], [fd], [], 1)
                continue
            data = data[n:]

    global _WAKE, GRAPHICS, CELL, _RUNS
    outermost, _RUNS = _RUNS == 0, _RUNS + 1
    if outermost:
        GRAPHICS = graphics_terminal()
    result = None
    wake_r, wake_w = os.pipe()          # wake(): a byte here (from a provider's thread, or a resize)
    os.set_blocking(wake_r, False)
    os.set_blocking(wake_w, False)
    _WAKE, resized = wake_w, None
    try:
        resized = signal.signal(signal.SIGWINCH, lambda *_: wake())
    except (ValueError, AttributeError):    # not the main thread, or no SIGWINCH: look now and then
        pass
    try:
        ttymod.setraw(fd)
        out('\033[?1049h\033[?25l\033[?2004h' + (HOLDING if outermost else ''))
        animate = not os.environ.get('CDECK_CALM')
        size, dirty, shown, redraw = None, True, [], None
        while True:
            if os.get_terminal_size(fd) != size:
                size, dirty, shown = os.get_terminal_size(fd), True, []
                CELL = cell_size(fd) or CELL
                IMAGES.forget()
            if c.changed():
                dirty = True
            if dirty:
                lines, _ = c.frame(*size, animate)
                text = update(lines, shown)
                sending = IMAGES.output(text) if GRAPHICS else ''
                out(sending + text)
                shown, dirty = lines, False
                if 'a=T' in sending:    # a new picture: kitty may not have it decoded when its cells come
                    shown = [None if PLACEHOLDER in l else l for l in lines]       # so draw them again
                    redraw = time.monotonic() + 0.15
            wait = c.due()
            if redraw is not None:
                wait = max(0, redraw - time.monotonic()) if wait is None else min(wait, max(0, redraw - time.monotonic()))
            if resized is None:
                wait = 0.5 if wait is None else min(wait, 0.5)
            ready, _, _ = select.select([fd, wake_r], [], [], wait)
            if wake_r in ready:
                try:
                    while os.read(wake_r, 512):
                        pass
                except BlockingIOError:
                    pass
            if redraw is not None and time.monotonic() >= redraw:
                redraw, dirty = None, True
            if fd not in ready:
                dirty = dirty or not ready      # its time came: draw; woken: if anything changed
                continue
            dirty = True
            data = os.read(fd, 1024).decode(errors='replace')
            if data == '\x1b':  # a lone Esc, or the start of an arrow split across reads
                more, _, _ = select.select([fd], [], [], 0.03)
                if more:
                    data += os.read(fd, 1024).decode(errors='replace')
            while PASTE_START in data and PASTE_END not in data[data.rfind(PASTE_START):]:
                more, _, _ = select.select([fd], [], [], 0.5)       # a long paste: all of it
                if not more:
                    break
                data += os.read(fd, 65536).decode(errors='replace')
            for k in keys(data):
                r = apply(c, k, opts)
                if r:
                    result = r
                    break
            if result:
                break
            if c.waiting:       # an action's turn with the terminal: step aside, then come back
                out('\033[?2004l\033[?25h\033[?1049l' + LETTING_GO)
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                try:
                    c.drain()
                finally:
                    ttymod.setraw(fd)
                    out('\033[?1049h\033[?25l\033[?2004h' + HOLDING)
                    size = None
    finally:
        _WAKE = None
        _RUNS -= 1
        if resized is not None:
            signal.signal(signal.SIGWINCH, resized)
        os.close(wake_r)
        os.close(wake_w)
        if outermost and GRAPHICS:
            out(IMAGES.clear())
            GRAPHICS = None
        elif GRAPHICS:
            IMAGES.forget()     # the canvas this one opened over draws afresh, pictures and all
        out(('\033[?2004l' + LETTING_GO if outermost else '') + '\033[?25h\033[?1049l')
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return result[1] if result and result[0] == 'done' else None


def dump(opts):
    """Headless: apply --keys, print the last frame (plain text, or with colour with --raw)."""
    import re
    c = Canvas(opts.start, opts)
    for k in keys(opts.keys.encode().decode('unicode_escape')):
        if apply(c, k, opts):
            break
    w, h = map(int, opts.size.split('x'))
    c.frame(w, h, animate=False)
    c.source.wait()
    lines, _ = c.frame(w, h, animate=False)
    for l in lines:
        print(l if opts.raw else re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', l).rstrip())


def theme(name, overrides):
    if isinstance(name, dict):
        t = theme(name.get('base', 'powerline'), {})
        t.update({k: v for k, v in name.items() if k != 'base'})
    elif name in THEMES:
        t = dict(THEMES[name])
    elif os.path.isfile(os.path.expanduser(name)):
        with open(os.path.expanduser(name)) as f:
            data = json.load(f)
        t = theme(data.pop('base', 'powerline'), {})
        t.update(data)
    else:
        sys.exit(f"treecanvas: no theme {name!r} (themes: {', '.join(THEMES)}, or a .json file)")
    t.update(overrides)
    return t


def parser():
    a = argparse.ArgumentParser(description='The file system as one canvas you move across.',
                                epilog=f'Any of these can be set in {CONFIG}, e.g. '
                                       '{"theme": "nord", "dead": 0, "colors": {"sel_bg": 31}}.')
    a.add_argument('start', nargs='?', default='.')
    a.add_argument('--theme', default='powerline', help=f"{', '.join(THEMES)}, or a .json file "
                   '({"base": "nord", "sel_bg": "#ff8800", …}) (powerline)')
    a.add_argument('--glyphs', choices=tuple(GLYPHS), default='powerline',
                   help='separators: powerline (a powerline font), round (a Nerd Font), plain (powerline)')
    a.add_argument('--color', choices=('auto', 'truecolor', '256'), default='auto',
                   help='24-bit colour, or the 256-colour palette (auto)')
    a.add_argument('--ls-colors', action=argparse.BooleanOptionalAction, default=None,
                   help="colour entries the way `ls` does, from LS_COLORS (the theme's default)")
    a.add_argument('--recent', action=argparse.BooleanOptionalAction, default=True,
                   help='recently changed files glow, fading over a week (default: on)')
    a.add_argument('--git', action=argparse.BooleanOptionalAction, default=True,
                   help='mark changed entries and show the branch (on)')
    a.add_argument('--limit', type=int, default=0, help='most children shown per folder, the rest as "↓ N more"; 0 = all, trailing off screen (0)')
    a.add_argument('--dead', type=float, default=0.3, help='dead-zone size as a fraction of the view; 0 = always centred (0.3)')
    a.add_argument('--across', type=float, default=0.4, help='where the focus sits across the view, 0..1 (0.4)')
    a.add_argument('--ease', type=float, default=0.3, help='camera easing per frame; 1 = jump (0.3)')
    a.add_argument('--fade', type=int, default=2, help='rows per dimming step in the focused column (2)')
    a.add_argument('--colmax', type=int, default=24, help='widest a column gets (24)')
    a.add_argument('--gap', type=int, default=5, help='columns between a name and its children (5)')
    a.add_argument('--peek', action=argparse.BooleanOptionalAction, default=False,
                   help="show the focused folder's contents before opening it")
    a.add_argument('--fold', action=argparse.BooleanOptionalAction, default=False,
                   help='close a branch when you move off it (classic tree)')
    a.add_argument('--tab', choices=('branch', 'cycle'), default='branch', help='Tab opens (branch) or moves down (cycle)')
    a.add_argument('--print', choices=('display', 'absolute', 'shell', 'fish'), default='display', dest='print_as',
                   help='how to print the choice: from here or ~ (display), absolute, or quoted for '
                        'bash/zsh (shell) or fish')
    a.add_argument('--init', choices=tuple(INIT), help='print the lines for your shell\'s rc file: '
                   'eval "$(python3 treecanvas.py --init bash)" (or zsh); in fish, '
                   'python3 treecanvas.py --init fish | source')
    a.add_argument('--cmd', default='cdi', help='the name --init gives the cd command (cdi)')
    a.add_argument('--key', default='ctrl-t', help='the key --init binds to put a path in at the cursor: '
                   'ctrl-<letter>, alt-<letter> or none (ctrl-t)')
    a.add_argument('--keys', help=argparse.SUPPRESS)
    a.add_argument('--size', default='120x36', help=argparse.SUPPRESS)
    a.add_argument('--raw', action='store_true', help=argparse.SUPPRESS)
    a.add_argument('--dirs-only', action='store_true', help='folders only (for choosing one)')
    a.add_argument('--on-start', action='store_true', help='begin on the start folder itself, not inside it')
    a.add_argument('--grey-rest', action=argparse.BooleanOptionalAction, default=True,
                   help="only the branch you're on keeps its colours; the rest go grey (on)")
    a.add_argument('--backdrop', choices=('auto', 'off'), default='auto',
                   help='the screen behind, dimmed, in the empty space (inside tmux) (auto)')
    a.add_argument('--pane', action=argparse.BooleanOptionalAction, default=None,
                   help="a reading pane beside the tree: the chosen file's start (off; on for --tree)")
    a.add_argument('--pane-width', type=float, default=0.45, help="the pane's share of the width (0.45)")
    a.add_argument('--tree', metavar='FILE', help='show a JSON file as the tree, not the file system: '
                   'objects are branches, everything else leaves shown in the pane')
    return a


def options(argv=(), skip=(), defaults=None, **settings):
    """Settings: the defaults (the caller's `defaults` over ours), then config.json (less the
    keys in `skip`), then `argv`, then `settings` (any flag's name, plus `theme` as a dict,
    `header` / `footer`: functions of the width returning a line for the top / the bottom, and
    `provider`: a Provider, for a tree that isn't the file system)."""
    a = parser()
    a.set_defaults(**(defaults or {}))
    overrides = {}
    path = CONFIG if os.path.isfile(CONFIG) or not os.path.isfile(OLD_CONFIG) else OLD_CONFIG
    if os.path.isfile(path):
        try:
            with open(path) as f:
                conf = json.load(f)
        except (OSError, ValueError) as e:
            sys.exit(f'treecanvas: {path}: {e}')
        overrides = {} if 'colors' in skip else conf.pop('colors', {})
        conf.pop('colors', None)
        conf = {k.replace('-', '_'): v for k, v in conf.items() if k not in skip}
        known = {x.dest for x in a._actions}
        unknown = set(conf) - known
        if unknown:
            print(f"treecanvas: {path}: ignoring {', '.join(sorted(unknown))}", file=sys.stderr)
        a.set_defaults(**{k: v for k, v in conf.items() if k in known})
    opts = a.parse_args(list(argv))
    opts.footer = opts.header = opts.provider = None
    for k, v in settings.items():
        setattr(opts, k, v)
    if opts.tree and not opts.provider:
        try:
            with open(os.path.expanduser(opts.tree)) as f:
                opts.provider = Tree(json.load(f), os.path.basename(opts.tree))
        except (OSError, ValueError) as e:
            sys.exit(f'treecanvas: {opts.tree}: {e}')
    opts.colours = theme(opts.theme, overrides)
    if opts.ls_colors is None:
        opts.ls_colors = opts.colours.get('ls_colors', True)
    opts.truecolor = truecolor_terminal() if opts.color == 'auto' else opts.color == 'truecolor'
    return opts


def pick(start='.', skip=(), defaults=None, **settings):
    """Let someone choose a path on the canvas; the absolute path (the chosen node's id, with a
    `provider`), or None if they cancelled."""
    return run(options((), skip, defaults, start=start, **settings))


def main(argv=None):
    opts = options(sys.argv[1:] if argv is None else argv)
    if opts.init:
        print(init(opts.init, opts.cmd, opts.key), end='')
        return
    if opts.keys is not None:
        dump(opts)
        return
    chosen = run(opts)
    if chosen is None:
        sys.exit(1)
    print(chosen if opts.provider or opts.print_as == 'absolute' else shell_word(chosen, opts.print_as == 'fish')
          if opts.print_as in ('shell', 'fish') else display(chosen))


if __name__ == '__main__':
    main()
