"""Plain terminal labels from external text."""
import unicodedata


def plain(value):
    return ''.join(c for c in str(value or '')
                   if c in '\n\t' or not unicodedata.category(c).startswith('C'))


def imap_utf7(name):
    """A folder name as IMAP servers send it (modified UTF-7, RFC 3501: `&BB4EQgQ,BEA-` for
    Отпр…) in plain Unicode. A name that isn't valid modified UTF-7 (`R&D`) is returned as is."""
    import base64, re
    if '&' not in name:
        return name

    def decode(m):
        chunk = m.group(1)
        if not chunk:
            return '&'
        data = chunk.replace(',', '/')
        raw = base64.b64decode(data + '=' * (-len(data) % 4), validate=True)
        if len(raw) % 2:
            raise ValueError
        return raw.decode('utf-16-be')
    try:
        out = re.sub(r'&([A-Za-z0-9+,]*)-', decode, name)
    except ValueError:      # bad base64 or UTF-16 (binascii.Error and UnicodeError are ValueErrors)
        return name
    return name if '&' in re.sub(r'&([A-Za-z0-9+,]*)-', '', name) else out
