"""Shared message identifiers, and how mail is shown; independent of any mail or chat backend."""
import email.utils, os, time

SEP = "\x1f"

BODY_WIDTH = 66       # columns a message's body wraps to on the canvas
BODY_LINES = 40       # lines of a body shown at first; ↓ more shows MORE_LINES more
MORE_LINES = 80

# Folders shown first, in this order, by the start of their name in English, German or Czech.
ORDER = (('INBOX',), ('Sent', 'Gesendet', 'Odeslan'), ('Drafts', 'Entwürfe', 'Koncept'),
         ('Archive', 'Archiv'), ('Starred', 'Markiert', 'S hvězdičkou'),
         ('Junk', 'Spam', 'Nevyžádan'), ('Trash', 'Deleted', 'Gelöscht', 'Papierkorb', 'Koš'))

ATTACHED = ('seg_fg', 'seg_bg')     # an attached file's chip: a block of its own, apart from the text
MIN_PICTURE = 16        # pixels: a picture in a body narrower or shorter is a spacer or a tracker


def file_kind(name, content_type=''):
    """What a file is, in a word, for its chip: PDF, IMAGE, ZIP, DOCX…"""
    ext = os.path.splitext(name or '')[1].lstrip('.').upper()
    if ext and len(ext) <= 5 and ext.isalnum():
        return ext
    main, _, sub = (content_type or '').partition('/')
    return (sub if main == 'application' and sub and len(sub) <= 8 else main or 'FILE').upper()


def size_text(size):
    """A file's size: 812 B, 34 KB, 2.1 MB."""
    if not isinstance(size, (int, float)):
        return ''
    if size < 1024:
        return f'{size:,.0f} B'
    if size < 1024 * 1024:
        return f'{size / 1024:,.0f} KB'
    return f'{size / 1024 / 1024:,.1f} MB'


def when(stamp, now=None):
    """A short date: the time today, the day this year, the day and year before."""
    t, now = time.localtime(stamp), time.localtime(now or time.time())
    if t[:3] == now[:3]:
        return time.strftime('%H:%M', t)
    day = f'{t.tm_mday} {time.strftime("%b", t)}'
    return day if t.tm_year == now.tm_year else f'{day} {t.tm_year}'


def who(header):
    """The name in a From line, else the address's mailbox."""
    name, addr = email.utils.parseaddr(header or '')
    return name or addr.split('@')[0] or '(nobody)'


def first_author(authors):
    """Who began a thread, from notmuch's authors ('Ann, Bo| Cy': matched ones before the |)."""
    return (authors or '').replace('|', ',').split(',')[0].strip() or '(nobody)'


def folder_name(rel):
    """How a folder is shown: Inbox, and Gmail's [Gmail]/ dropped."""
    if rel == 'INBOX':
        return 'Inbox'
    return rel[len('[Gmail]/'):] if rel.startswith('[Gmail]/') else rel


def folder_rank(rel):
    name = folder_name(rel)
    hit = next((i for i, names in enumerate(ORDER)
                if any(name.casefold().startswith(o.casefold()) for o in names)), len(ORDER))
    return hit, name.casefold()
