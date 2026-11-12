"""Thunderbird's mail as the native Emails are drawn: accounts, their folders in the same order,
a folder's threads by date or sender, and a message's subject, people, date, body and files.
Lazy: RPC work never blocks canvas drawing."""
import datetime
import email.utils
import hashlib
import os
import queue
import subprocess
import threading
import time
import types
import zlib
from twigs import core as fc
from twigs.kit.dates import when_group
from twigs.kit.text import imap_utf7, plain
from twigs.apps.msg.common import (BODY_LINES, copied, BODY_WIDTH, MORE_LINES, ATTACHED, MIN_PICTURE, file_kind,
                                   folder_name, folder_rank, size_text, when, who)
from twigs.apps.msg.thunderbird import short_name
from twigs.apps.msg import compose, files
from .connection import Client
from . import settings

PAGE = 200            # messages asked for at a time; threads and groups are made from what's loaded
GROUPINGS = ('date', 'sender', 'all')
BEACON_DAYS = 30
# Thunderbird's folder types, ranked as ORDER ranks the native folders by name
RANKS = dict(inbox=0, sent=1, drafts=2, archive=3, junk=5, trash=6)
CONTENT = ('tb-subject', 'tb-person', 'tb-when', 'tb-body', 'tb-lines', 'tb-attached', 'tb-attachment')
LOADS = ('tb-root', 'tb-account', 'tb-folder')     # what's fetched as a whole (beside messages)


def key(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:24]


def stamp(date):
    """Thunderbird's ISO date as seconds; 0 when it has none."""
    try:
        return datetime.datetime.fromisoformat(str(date).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0


def picture_file(a):
    """The local file of an attachment (as getMessage gives it) when it's a picture worth drawing:
    not a spacer or tracking pixel. None otherwise."""
    path = local_path(a.get('filePath'))
    if not path or not str(a.get('contentType') or '').startswith('image/') or not os.path.isfile(path):
        return None
    size = fc.image_size(path)
    return None if size and min(size) < MIN_PICTURE else path


def local_path(path):
    """An attachment the extension saved, as a path here: a Windows path (Thunderbird on Windows,
    from WSL) through wslpath."""
    if not path:
        return None
    if len(path) > 2 and path[1] == ':' and os.name == 'posix':
        try:
            got = subprocess.run(['wslpath', '-u', path], capture_output=True, text=True, timeout=5)
            return got.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None
    return path


def folders(rows):
    """Thunderbird's folder rows (each with its depth) as the native list: every folder by its
    path from the account, Gmail's [Gmail] container dropped, in the native order."""
    out, stack = [], []
    for row in rows:
        depth = max(0, int(row.get('depth', 0)))
        del stack[depth:]
        row = dict(row, name=imap_utf7(row['name']))     # the path stays as the server has it
        stack.append(row['name'])
        if row['name'] == '[Gmail]' and depth == 0:
            continue
        rel = '/'.join(stack)
        rank = RANKS.get(row.get('type'), folder_rank(rel)[0])
        out.append(dict(row, rel=rel, rank=rank))
    return sorted(out, key=lambda f: (f['rank'], folder_name(f['rel']).casefold()))


def threads(messages):
    """Messages as threads, newest first: each oldest message first, and whether any is unread."""
    found = {}
    for m in messages:
        found.setdefault((m['folderPath'], m.get('threadId') or m['id']), []).append(dict(m, stamp=stamp(m.get('date'))))
    out = []
    for tid, msgs in found.items():
        msgs.sort(key=lambda m: m['stamp'])
        out.append(dict(tid=tid, subject=msgs[0].get('subject') or '(no subject)', messages=msgs,
                        date=msgs[-1]['stamp'], count=len(msgs), author=who(msgs[0].get('author')),
                        unread=any(m.get('read') is False for m in msgs),
                        flagged=any(m.get('flagged') for m in msgs)))
    return sorted(out, key=lambda t: -t['date'])


def groups(found, grouping, now=None):
    """Threads in groups, the newest group first: by when each thread's newest came, or who began it."""
    out = {}
    for t in found:
        out.setdefault(when_group(t['date'], now) if grouping == 'date' else t['author'], []).append(t)
    return sorted(out.items(), key=lambda g: -g[1][0]['date'])


class Thunderbird(fc.Provider):
    def __init__(self, config, client_factory=Client, grouping='date'):
        self.config, self.client_factory = config, client_factory
        self.grouping = grouping
        self.client = None
        self.snapshots, self.errors, self.pending, self.queries, self.versions = {}, {}, {}, {}, {}
        self.lines = {}                         # a message's id: how many lines of its body show
        self.working = 0                        # actions queued or under way (do())
        self.mine = set()                       # the selected accounts' addresses, for "you"
        self.lock = threading.Lock()
        self.jobs = queue.Queue()
        self.closed = False
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def root(self):
        return fc.Node('/thunderbird', 'Emails', True, source=self, kind='tb-root')

    def forget(self):
        """Ask again which accounts there are (the shown ones changed in Settings)."""
        path = self.root().path
        with self.lock:
            self.snapshots.pop(path, None)
            self.errors.pop(path, None)
        self.invalidate(path, deep=True)

    @staticmethod
    def message_of(node):
        """The message `node` reads: its own, or a thread of one's."""
        if node.kind == 'tb-message':
            return node.data
        if node.kind == 'tb-thread' and node.data['count'] == 1:
            return node.data['messages'][0]
        return None

    def request(self, node, offset=0, force=False):
        with self.lock:
            if self.closed or (node.path in self.pending and not force):
                return
            version = self.versions.get(node.path, 0) + 1
            self.versions[node.path] = self.pending[node.path] = version
            query = self.queries.get(node.path, '')
        self.jobs.put((node, offset, query, version))

    def run(self):
        while True:
            job = self.jobs.get()
            if job is None or self.closed:
                return
            if callable(job):           # something you asked to be done (do()): it reports itself
                job()
                continue
            node, offset, query, version = job
            with self.lock:
                if self.pending.get(node.path) != version:
                    continue
            try:
                if self.client is None or self.client.closed:
                    self.client = self.client_factory(self.config)
                if node.kind == 'tb-root':
                    accounts = self.client.call('listAccounts')
                    selected, _ = settings.selection(accounts, self.config)
                    data = [a for a in accounts if a['id'] in selected and a['id'] in self.config['accounts']]
                    self.mine = {i['email'].casefold() for a in data for i in a.get('identities', []) if i.get('email')}
                elif node.kind == 'tb-account':
                    data = folders(self.client.call('listFolders', dict(accountId=node.data['id'], format='objects')))
                elif node.kind == 'tb-folder':
                    data = self.client.call('searchMessages', dict(query=query, folderPath=node.data['path'],
                        includeSubfolders=False, maxResults=PAGE, offset=offset, sortOrder='desc', fullRecipients=True))
                    if not isinstance(data, dict) or not isinstance(data.get('messages'), list):
                        raise OSError('Thunderbird did not return a paginated message list; update the extension')
                    if offset:
                        with self.lock:
                            previous = self.snapshots.get(node.path, {}).get('messages', [])
                        seen = {(m['folderPath'], m['id']) for m in previous}
                        data = dict(data, messages=previous + [m for m in data['messages'] if (m['folderPath'], m['id']) not in seen])
                elif self.message_of(node) is not None:
                    m = self.message_of(node)
                    data = self.client.call('getMessage', dict(messageId=m['id'], folderPath=m['folderPath'],
                        bodyFormat='text', saveAttachments=True, includeInlineImages=False))
                    if not isinstance(data, dict) or data.get('bodyIsHtml'):
                        raise OSError('Thunderbird returned an unsupported message format')
                else:
                    continue
                with self.lock:
                    if self.pending.get(node.path) == version:
                        self.snapshots[node.path] = data
                        self.errors.pop(node.path, None)
            except (OSError, ValueError, KeyError, TypeError) as e:
                with self.lock:
                    if self.pending.get(node.path) == version:
                        self.errors[node.path] = self.explain(e)
                if self.client:
                    self.client.close()
            finally:
                with self.lock:
                    if self.pending.get(node.path) == version:
                        self.pending.pop(node.path, None)
                if not self.closed:
                    self.invalidate(node.path, deep=True)

    @staticmethod
    def explain(error):
        """What to do about a failed request: Thunderbird stopped or its extension off say so."""
        try:
            from . import host
            hint = host.hint()
        except (OSError, ValueError):
            hint = None
        return hint or str(error)

    # -- the tree
    def children(self, node):
        k = node.kind
        if k == 'tb-group':
            return [self.thread_node(t, node) for t in node.data['threads']]
        if k == 'tb-thread' and node.data['count'] > 1:
            return [self.message_node(m, node) for m in node.data['messages']]
        if k not in ('tb-root', 'tb-account', 'tb-folder') and self.message_of(node) is None:
            return []
        with self.lock:
            data, error = self.snapshots.get(node.path), self.errors.get(node.path)
        if data is None and not error:
            self.request(node)
        out = []

        def add(suffix, label, branch=False, kind='tb-note', data=None):
            out.append(fc.Node(node.path+'/'+suffix, plain(label), branch, node, kind, data=data))
        if error:
            add('error', error+('\nCtrl+L to retry' if self.message_of(node) else '\nCtrl+R to retry'))
        if data is None:
            if not error:
                add('loading', 'Loading Thunderbird…')
            return out
        if k == 'tb-root':
            names = set()
            for a in data:
                address = next((i['email'] for i in a.get('identities', []) if i.get('email')), '')
                name = self.config.get('names', {}).get(a['id']) or \
                    (short_name(address) if address and a['name'] == address else a['name'])
                while name in names:
                    name += '-2'
                names.add(name)
                add('a'+key(a['id']), name, True, 'tb-account', a)
        elif k == 'tb-account':
            for f in data:
                add('f'+key(f['path']), folder_name(f['rel']), True, 'tb-folder', f)
        elif k == 'tb-folder':
            query = self.queries.get(node.path, '')
            if query:
                add('query', 'Search: '+query+'\nCtrl+F to change or clear')
            found = threads(data['messages'])
            if self.grouping == 'all':
                out += [self.thread_node(t, node) for t in found]
            else:
                for name, ts in groups(found, self.grouping):
                    add(self.grouping+':'+key(name), name, True, 'tb-group',
                        dict(threads=ts, unread=sum(t['unread'] for t in ts)))
            if data.get('truncated'):
                add('partial', data.get('message') or 'Partial results; narrow the search or folder')
            refresh = data.get('folderRefresh', {})
            if refresh and not refresh.get('updateConfirmed'):
                add('freshness', refresh.get('warning') or 'Showing available mail; server refresh is unconfirmed')
            if data.get('hasMore'):
                # Offset is the server's page position, independent of duplicate suppression.
                add('more', '↓ More messages', False, 'tb-more',
                    int(data.get('offset', 0))+int(data.get('limit', PAGE)))
        else:
            out += self.contents(node, data)
        if not out:
            add('empty', 'No results' if k == 'tb-folder' else 'No accounts or folders selected')
        return out

    def thread_node(self, t, parent):
        return fc.Node(parent.path+'/t'+key(t['tid']), plain(t['subject']), True, parent, 'tb-thread', data=t)

    def message_node(self, m, parent):
        return fc.Node(parent.path+'/m'+key(m['folderPath']+'\0'+m['id']),
                       plain(f"{who(m.get('author'))} · {when(m['stamp'])}"), True, parent, 'tb-message', data=m)

    def contents(self, node, data):
        """What a message says, as branches beside it, as the native Emails show it."""
        m, out = self.message_of(node), []

        def add(kind, suffix, label, extra=None):
            out.append(fc.Node(node.path+'/'+suffix, plain(label), False, node, kind, data=extra))
        add('tb-subject', 'subject', data.get('subject') or m.get('subject') or '(no subject)')
        people = lambda field: [(n, a) for n, a in email.utils.getaddresses([data.get(field) or '']) if a or n]
        to, cc = people('recipients'), people('ccList')
        everyone = [('from', p) for p in people('author')] + [('to', p) for p in to[:6]] + [('cc', p) for p in cc[:4]]
        for i, (role, (name, address)) in enumerate(everyone):
            add('tb-person', f'p{i}', name or address, dict(role=role, address=address))
        hidden = max(0, len(to) - 6) + max(0, len(cc) - 4)
        if hidden:
            add('tb-person', 'p+', f'and {hidden} more', dict(role='', address=''))
        sent = stamp(data.get('date') or m.get('date'))
        if sent:
            add('tb-when', 'when', time.strftime('%a %-d %b %Y, %H:%M', time.localtime(sent)))
        shown = self.lines.get(m['id'], BODY_LINES)
        lines, paragraph, n = (data.get('body') or '').replace('\r', '').strip().split('\n'), [], 0
        for line in lines[:shown] + ['']:
            if line.strip():
                paragraph.append(line.rstrip())
            elif paragraph:
                add('tb-body', f'b{n}', '\n'.join(paragraph))
                paragraph, n = [], n + 1
        if not n:
            add('tb-body', 'b0', '(no readable body)')
        if len(lines) > shown:
            add('tb-lines', 'lines', f'↓ {len(lines) - shown} more lines', dict(id=m['id']))
        if data.get('bodyNote'):
            add('tb-note', 'body-note', data['bodyNote'])
        files_ = data.get('attachments', [])
        info = lambda a, inline: dict(size=a.get('size'), path=local_path(a.get('filePath')), name=a.get('name'),
                                      type=a.get('contentType') or '', inline=inline)
        for i, a in enumerate(files_):      # pictures in the body: drawn there, after the text
            if a.get('isInline') and picture_file(a):
                add('tb-attachment', f'a{i}', f"📷 {a.get('name') or 'picture'}", info(a, True))
        attached = [(i, a) for i, a in enumerate(files_) if not a.get('isInline')]
        if attached:            # what's attached, set apart: a heading, then each file a chip
            sizes = [a.get('size') for _, a in attached]
            add('tb-attached', 'attached', f'📎 {len(attached)} attached',
                dict(size=sum(sizes) if all(isinstance(z, (int, float)) for z in sizes) else None))
        for i, a in attached:
            name = a.get('name') or 'Attachment'
            add('tb-attachment', f'a{i}', f"{file_kind(name, a.get('contentType'))}  {name}", info(a, False))
        return out

    # -- the pane: a message's pictures, in the body or attached
    def reading(self, node):
        """The message node (or thread of one) that `node` is, or is part of, and its fetched content."""
        while node is not None and node.kind in CONTENT + ('tb-note',):
            node = node.parent
        if node is None or self.message_of(node) is None:
            return None, None
        with self.lock:
            return node, self.snapshots.get(node.path)

    def pictures(self, node):
        _, data = self.reading(node)
        if not isinstance(data, dict):
            return []
        return [fc.Picture(path, a.get('name')) for a in data.get('attachments', []) if (path := picture_file(a))]

    def picture(self, node):
        """A picture in the body, or attached, drawn under its line (where the terminal can)."""
        if node.kind != 'tb-attachment':
            return None
        return picture_file(dict(filePath=node.data.get('path'), contentType=node.data.get('type')))

    def pane_for(self, node):
        """The pane only shows a message's pictures, where the canvas can't draw them itself."""
        return fc.GRAPHICS != 'kitty' and bool(self.pictures(node))

    def preview(self, node):
        return self.pictures(node) or None

    def choose(self, node):
        if node.kind == 'tb-more':
            self.request(node.parent, offset=node.data)
        elif node.kind == 'tb-lines':
            self.lines[node.data['id']] = self.lines.get(node.data['id'], BODY_LINES) + MORE_LINES
            self.invalidate(node.parent.path, deep=True)
        return None if node.is_dir else fc.STAY

    def actions(self, node):
        new = ('\x0e', 'new', '⌃N new')
        msgs = self.targets(node)
        if not msgs:            # an account, a folder, a group of threads, a note
            out = [new, ('\x12', 'refresh', '⌃R refresh')]
            if node.kind == 'tb-account':
                out.append(('\x05', 'rename', '⌃E rename'))
            if node.parent is not None and self.message_of(node.parent):   # a note beside a message: its error
                out.append(('\x0c', 'reload', '⌃L reload'))
            if node.kind in ('tb-folder', 'tb-group'):
                nxt = GROUPINGS[(GROUPINGS.index(self.grouping) + 1) % len(GROUPINGS)]
                out += [('\x06', 'search', '⌃F search folder'),
                        ('\x0c', 'group', f'⌃L by {nxt}' if nxt != 'all' else '⌃L not grouped')]
            return out
        unread = any(m.get('read') is False for m in msgs)
        flagged = any(m.get('flagged') for m in msgs)
        out = [('\x12', 'reply', '⌃R reply'), ('\x01', 'reply-all', '⌃A reply all'),
               ('\x06', 'forward', '⌃F forward'),
               ('\x0b', 'read', '⌃K mark read' if unread else '⌃K mark unread'),
               ('\x10', 'flag', '⌃P unflag' if flagged else '⌃P flag'),
               ('\x19', 'archive', '⌃Y archive'), ('\x16', 'move', '⌃V move'),
               ('\x04', 'delete', '⌃D delete' if self.in_trash(node) else '⌃D trash'), new,
               ('\x0c', 'reload', '⌃L reload')]
        if node.kind == 'tb-attachment':
            out = [('\x0f', 'open-one', '⌃O open'), ('\x13', 'save-one', '⌃S save')] + out
        elif self.attached(node):
            out += [('\x0f', 'open', '⌃O open attached'), ('\x13', 'save', '⌃S save attached')]
        return out

    # -- what an action on a message is for, and where
    def targets(self, node):
        """The messages an action on `node` is for: a message's own (a part of it, its own), or all
        of a thread's. [] for anything else."""
        while node is not None and node.kind in CONTENT:
            node = node.parent
        if node is None:
            return []
        if node.kind == 'tb-message':
            return [node.data]
        if node.kind == 'tb-thread':
            return list(node.data['messages'])
        return []

    def newest(self, node):
        """The message a reply or forward from `node` is to: its own, or a thread's latest."""
        msgs = self.targets(node)
        return max(msgs, key=lambda m: m.get('stamp', 0)) if msgs else None

    @staticmethod
    def up(node, kind):
        while node is not None and node.kind != kind:
            node = node.parent
        return node

    def folder_list(self, node):
        """The account's folders, as listFolders gave them (with rel and rank), or []."""
        account = self.up(node, 'tb-account')
        with self.lock:
            got = self.snapshots.get(account.path) if account is not None else None
        return got if isinstance(got, list) else []

    def in_trash(self, node):
        folder = self.up(node, 'tb-folder')
        return folder is not None and (folder.data.get('type') == 'trash' or folder.data.get('rank') == RANKS['trash'])

    def attached(self, node):
        """The message's attachments the extension saved (not the pictures in its body): [(name, path)]."""
        _, data = self.reading(node)
        if not isinstance(data, dict):
            return []
        return [(a.get('name') or 'attachment', local_path(a.get('filePath')))
                for a in data.get('attachments', []) if not a.get('isInline') and a.get('filePath')]

    def show(self, node, msgs, change=None):
        """What's shown changes at once, before Thunderbird has done it: `msgs` take `change`
        (read, flagged…), or leave the folder with None. A failure fetches the folder afresh."""
        folder = self.up(node, 'tb-folder')
        if folder is None:
            return None
        chosen, unread, gone = {(m['folderPath'], m['id']) for m in msgs}, 0, 0
        with self.lock:
            snap = self.snapshots.get(folder.path)
            if isinstance(snap, dict) and isinstance(snap.get('messages'), list):
                kept = []
                for m in snap['messages']:
                    if (m['folderPath'], m['id']) in chosen:
                        was = m.get('read') is False
                        if change is None:
                            unread, gone = unread - was, gone + 1
                            continue
                        m.update(change)
                        unread += (m.get('read') is False) - was
                    kept.append(m)
                snap['messages'] = kept
        d = folder.data
        if unread and isinstance(d.get('unreadMessages'), int):
            d['unreadMessages'] = max(0, d['unreadMessages'] + unread)
        if gone and isinstance(d.get('totalMessages'), int):
            d['totalMessages'] = max(0, d['totalMessages'] - gone)
        if folder.parent is not None:
            self.invalidate(folder.parent.path)        # the folder's count, in its account
        self.invalidate(folder.path, deep=True)
        return folder

    def do(self, key, busy, work, folder=None):
        """work(client) on the worker, after what's loading: its chip in the bar spins (`busy`,
        or none at all when None) and then says what work() returned, (text, state) or text for ✓,
        or why it failed. A failure fetches `folder` again, showing it as it really is."""
        if busy:
            self.status(key, busy)

        def job():
            try:
                if self.client is None or self.client.closed:
                    self.client = self.client_factory(self.config)
                said = work(self.client)
            except (OSError, ValueError, KeyError, TypeError) as e:
                self.status(key, self.explain(e), 'failed')
                if folder is not None:
                    self.request(folder, force=True)
                return
            finally:
                with self.lock:
                    self.working -= 1
            text, state = said if isinstance(said, tuple) else (said, 'ok')
            if text:
                self.status(key, text, state)
            elif busy:
                self.status(key, busy, 'gone')
        with self.lock:
            self.working += 1
        self.jobs.put(job)

    def update(self, node, msgs, quiet=False, **change):
        """updateMessage for `msgs`, a folder at a time; what's shown changes first."""
        moving = 'moveTo' in change or change.get('trash')
        flags = {k: v for k, v in change.items() if k in ('read', 'flagged')}
        folder = self.show(node, msgs, None if moving else flags)
        by_folder = {}
        for m in msgs:
            by_folder.setdefault(m['folderPath'], []).append(m['id'])
        done = change.pop('said', 'done')

        def work(client):
            for path, ids in by_folder.items():
                client.call('updateMessage', dict(messageIds=ids, folderPath=path, **change))
            return None if quiet else done
        self.do('tb-update', None if quiet else 'asking Thunderbird…', work, folder)

    def focus(self, node):
        """Going into a message (onto what it says) reads it, as Thunderbird would, unless
        mark_read is false in thunderbird.json."""
        if node.kind in CONTENT and self.config.get('mark_read', True):
            unread = [m for m in self.targets(node) if m.get('read') is False]
            if unread:
                self.update(node, unread, quiet=True, read=True)

    def act(self, node, name, canvas):
        msgs = self.targets(node)
        n = len(msgs)
        some = 'it' if n == 1 else f'{n} messages'
        if name in ('reply', 'reply-all', 'forward', 'new'):
            self.write(canvas, node, name)
        elif name == 'read':
            read = any(m.get('read') is False for m in msgs)
            self.update(node, msgs, read=read, said='marked read' if read else 'marked unread')
        elif name == 'flag':
            flag = not any(m.get('flagged') for m in msgs)
            self.update(node, msgs, flagged=flag, said='flagged' if flag else 'unflagged')
        elif name == 'archive':
            here = self.up(node, 'tb-folder')
            archive = next((f for f in self.folder_list(node) if f.get('rank') == RANKS['archive']), None)
            if archive is None:
                canvas.say('this account has no Archive folder: ⌃V moves it to another')
            elif here is not None and archive['path'] == here.data.get('path'):
                canvas.say('already in the archive')
            else:
                self.update(node, msgs, moveTo=archive['path'], said=f'archived {some}')
        elif name == 'move':
            self.move(canvas, node, msgs)
        elif name == 'delete':
            if self.in_trash(node):
                def gone(k):
                    if k == 'y':
                        folder = self.show(node, msgs)
                        path, ids = msgs[0]['folderPath'], [m['id'] for m in msgs]
                        self.do('tb-update', 'deleting…', lambda c: (
                            c.call('deleteMessages', dict(messageIds=ids, folderPath=path)), f'deleted {some}')[1], folder)
                canvas.ask(f'delete {some} for good?  y yes  n no', gone, choices='yn')
            else:
                self.update(node, msgs, trash=True, said=f'moved {some} to Trash')
        elif name in ('open-one', 'save-one'):
            self.use(canvas, node.data.get('path'), name == 'open-one')
        elif name == 'open':
            found = self.attached(node)
            if len(found) == 1:
                self.use(canvas, found[0][1], True)
            else:
                shown = found[:9]
                names = '  '.join(f'{i + 1} {fc.clip(name, 24)}' for i, (name, _) in enumerate(shown))
                canvas.ask(f'open which?  {names}', lambda k: self.use(canvas, shown[int(k) - 1][1], True),
                           choices=''.join(str(i + 1) for i in range(len(shown))))
        elif name == 'save':
            saved = [files.copy_to(p, files.downloads()) for _, p in self.attached(node) if p and os.path.isfile(p)]
            canvas.say(f'saved {len(saved)} to {files.downloads()}' if saved else 'nothing attached could be saved')
        elif name == 'search':
            while node.parent and node.kind != 'tb-folder':
                node = node.parent
            if node.kind != 'tb-folder':
                canvas.say('Open a folder to search its messages')
                return fc.STAY
            def search(text):
                with self.lock:
                    self.queries[node.path] = text.strip()
                    self.snapshots.pop(node.path, None)
                    self.errors.pop(node.path, None)
                self.request(node, force=True)
                self.invalidate(node.path, deep=True)
            canvas.ask('Search this folder (empty clears)', search, text=self.queries.get(node.path, ''))
        elif name == 'group':
            self.grouping = GROUPINGS[(GROUPINGS.index(self.grouping) + 1) % len(GROUPINGS)]
            while node.kind != 'tb-folder':
                node = node.parent
            self.invalidate(node.path, deep=True)
            canvas.say(f'by {self.grouping}' if self.grouping != 'all' else 'not grouped')
        elif name == 'refresh':
            while node.parent and node.kind not in LOADS and self.message_of(node) is None:
                node = node.parent
            self.request(node, force=True)
            canvas.say('Refreshing from Thunderbird')
        elif name == 'reload':
            # ⌃R on a message replies: this fetches the message (a thread of several: its folder) again
            while node.parent and node.kind not in ('tb-message', 'tb-thread', 'tb-folder'):
                node = node.parent
            if self.message_of(node) is None:
                node = self.up(node, 'tb-folder') or node
            self.request(node, force=True)
            canvas.say('Fetching the message again' if self.message_of(node) else 'Refreshing from Thunderbird')
        elif name == 'rename':
            self.rename(canvas, node)
        return None

    def rename(self, canvas, node):
        """Name an account as twigs shows it; kept in twigs's Thunderbird settings, not Thunderbird's."""
        ident = node.data['id']

        def done(text):
            try:
                self.config['names'] = settings.rename(ident, plain(text), self.config)
            except (OSError, ValueError) as e:
                canvas.say(f'could not rename: {e}')
                return None
            self.invalidate(self.root().path, deep=True)
            canvas.say(f'renamed to {text.strip()}' if text.strip() else 'named as in Thunderbird again')
            return None
        canvas.ask('Name this account (empty: as Thunderbird names it)', done, text=node.name)

    def use(self, canvas, path, opening):
        """Open an attachment the extension saved, or save it to Downloads."""
        if not path or not os.path.isfile(path):
            canvas.say('Thunderbird did not save that attachment; ⌃L on the message fetches it again')
        elif opening:
            files.open_file(path)
            canvas.say(f'opening {os.path.basename(path)}')
        else:
            canvas.say(f'saved to {files.copy_to(path, files.downloads())}')

    def move(self, canvas, node, msgs):
        """Move to a folder of the account, named as you like: its name, its path, or part of either."""
        here = self.up(node, 'tb-folder')
        choices = [f for f in self.folder_list(node) if not here or f['path'] != here.data.get('path')]

        def done(text):
            said = text.strip().casefold()
            if not said:
                return None
            exact = [f for f in choices if said in (f['rel'].casefold(), folder_name(f['rel']).casefold(), f['name'].casefold())]
            found = exact or [f for f in choices if said in f['rel'].casefold() or said in folder_name(f['rel']).casefold()]
            if len(found) != 1:
                names = ', '.join(folder_name(f['rel']) for f in found[:6])
                canvas.say(f'{len(found)} folders match: {names}' if found else f'no folder called {text.strip()}')
                return None
            target = found[0]
            self.update(node, msgs, moveTo=target['path'], said=f'moved to {folder_name(target["rel"])}')
            return None
        if not choices:
            canvas.say('no folders to move it to')
            return
        canvas.ask('move to which folder?', done)

    # -- writing: in your editor, as the native Emails do; Thunderbird sends it
    def identity(self, node):
        """(name, address) to write from: the account's default identity, or the first account's."""
        account = self.up(node, 'tb-account')
        if account is not None:
            ids = account.data.get('identities') or []
        else:
            with self.lock:
                accounts = self.snapshots.get(self.root().path) or []
            ids = accounts[0].get('identities', []) if accounts else []
        ident = next((i for i in ids if i.get('isDefault')), ids[0] if ids else {})
        return ident.get('name') or '', ident.get('email') or ''

    def write(self, canvas, node, kind):
        """A draft for your editor: headers on top (To, Cc, Bcc, and Subject for new mail), what
        you write under them. Thunderbird quotes the original in a reply and includes it in a
        forward, and gives either its subject."""
        name, address = self.identity(node)
        m = None if kind == 'new' else self.newest(node)
        if kind != 'new' and m is None:
            canvas.say('choose a message first')
            return
        head = {'From': email.utils.formataddr((name, address)) if address else '', 'To': '', 'Cc': '', 'Bcc': ''}
        if kind == 'new':
            head['Subject'] = ''
        else:
            label = 'Forwarding' if kind == 'forward' else 'Replying-To'
            head[label] = f"{who(m.get('author'))}: {plain(m.get('subject') or '(no subject)')}"
        if kind in ('reply', 'reply-all'):
            head['To'] = m.get('author') or ''
        if kind == 'reply-all':
            own = {address.casefold()} | self.mine
            sender = {a.casefold() for _, a in email.utils.getaddresses([m.get('author') or ''])}
            seen = own | sender
            rest = []
            for pair in email.utils.getaddresses([m.get('recipients') or '', m.get('ccList') or '']):
                if pair[1] and pair[1].casefold() not in seen:
                    seen.add(pair[1].casefold())
                    rest.append(email.utils.formataddr(pair))
            head['Cc'] = ', '.join(rest)
        folder = os.path.join(compose.DRAFTS, 'thunderbird')
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, time.strftime('%Y%m%d-%H%M%S') + f'-{os.getpid()}-{len(os.listdir(folder))}.eml')
        compose.write(path, head, [], '')
        ctx = dict(kind=kind, message=m, before={h: head[h] for h in ('To', 'Cc', 'Bcc')})
        self.edit(canvas, path, ctx)

    def edit(self, canvas, path, ctx):
        def run():
            fc.terminal(compose.edit_command(path))
            self.after_edit(canvas, path, ctx)
        canvas.outside(run)

    def after_edit(self, canvas, path, ctx, note=''):
        headers, attach, body = compose.parse(path)
        to = headers.get('To') or headers.get('Cc') or headers.get('Bcc') or 'nobody'
        extra = f', {len(attach)} attached' if attach else ''
        keep = ctx['kind'] != 'forward'          # Thunderbird drafts a reply or a new mail, not a forward

        def done(k):
            if k == 'y':
                problem = self.unsendable(headers, attach, ctx)
                if problem:
                    self.after_edit(canvas, path, ctx, problem + ': ')
                else:
                    self.send(path, ctx)
            elif k == 'e':
                self.edit(canvas, path, ctx)
            elif k == 'a':
                def choose():
                    chosen = fc.pick(files.downloads())
                    if chosen and os.path.isfile(chosen):
                        compose.attach(path, chosen)
                    self.after_edit(canvas, path, ctx)
                canvas.outside(choose)
            elif k == 'd' and keep:
                self.send(path, ctx, draft=True)
            elif k == 'q':
                compose.discard(path)
                canvas.say('thrown away')
        canvas.ask(f'{note}to {fc.clip(to, 40)}{extra}:  y send  e edit  a attach  ' +
                   ('d save to Drafts  ' if keep else '') + 'q throw away', done,
                   choices='yeaq' + ('d' if keep else ''))

    @staticmethod
    def unsendable(headers, attach, ctx):
        """Why the draft can't go as it is, or None."""
        if not any(compose.addresses(headers.get(h, '')) for h in ('To', 'Cc', 'Bcc')):
            return 'nobody to send it to'
        missing = [a for a in attach if not os.path.isfile(a)]
        if missing:
            return 'no such file to attach: ' + ', '.join(os.path.basename(a) for a in missing)
        return None

    def send(self, path, ctx, draft=False):
        """The draft to Thunderbird, which sends it (or, with `draft`, keeps it in its Drafts).
        If Thunderbird insists on reviewing what's sent (Block skipReview, in the extension's
        options), a reply or a new mail goes to its Drafts instead, to send from there."""
        headers, attach, body = compose.parse(path)
        kind, m = ctx['kind'], ctx['message']
        args = dict(body=body.rstrip() + '\n', isHtml=False)
        sender = compose.addresses(headers.get('From', ''))
        if sender:
            args['from'] = sender[0]
        if attach:
            args['attachments'] = [os.path.abspath(a) for a in attach]
        if kind == 'new':
            args.update(to=headers.get('To', ''), cc=headers.get('Cc', ''), bcc=headers.get('Bcc', ''),
                        subject=headers.get('Subject', ''))
        else:
            args.update(messageId=m['id'], folderPath=m['folderPath'])
            for h in ('To', 'Cc', 'Bcc'):       # only what you changed: else Thunderbird's own (Reply-To…)
                if kind == 'forward' or headers.get(h, '') != ctx['before'][h]:
                    args[h.lower()] = headers.get(h, '')
            if kind != 'forward':
                args['replyAll'] = kind == 'reply-all'
        tool = dict(new='sendMail', forward='forwardMessage').get(kind, 'replyToMessage')
        who_to = ', '.join(a for h in ('To', 'Cc', 'Bcc') for a in compose.addresses(headers.get(h, ''))) or 'them'

        def to_drafts(client):
            if kind == 'new':
                client.call('saveDraft', {k: v for k, v in args.items() if k != 'replyAll'})
            else:
                client.call('replyToMessage', dict(args, saveAsDraft=True))

        def work(client):
            try:
                if draft:
                    to_drafts(client)
                    compose.discard(path)
                    return 'saved to Drafts'
                client.call(tool, dict(args, skipReview=True))
            except OSError as e:
                if draft or 'skipReview' not in str(e):
                    raise OSError(f'{e} (your text is kept in {path})') from None
                if kind == 'forward':
                    raise OSError('Thunderbird reviews what is sent (Block skipReview, in Thunderbird MCP\'s '
                                  f'options), so it was not sent; your text is kept in {path}') from None
                to_drafts(client)
                compose.discard(path)
                return ('not sent: Thunderbird reviews what is sent (Block skipReview, in Thunderbird MCP\'s '
                        'options), so it is in Drafts to send from Thunderbird', 'failed')
            compose.discard(path)
            return f'sent to {who_to}'
        self.do('tb-send', 'saving…' if draft else 'sending…', work)

    # -- how it looks: as the native Emails
    def count(self, node):
        k, d = node.kind, node.data
        if k == 'tb-folder':
            return (f"{d['unreadMessages']:,}", 'git_dirty', True) if d.get('unreadMessages') else None
        if k == 'tb-group':
            return f"{len(d['threads']):,}", 'stub', False
        if k == 'tb-thread' and d['count'] > 1:
            return str(d['count']), 'stub', False
        if k == 'tb-person' and d['address']:
            return f"{d['role']}  {d['address']}", 'stub', False
        if k in ('tb-attachment', 'tb-attached') and isinstance(d.get('size'), (int, float)):
            return size_text(d['size']), 'stub', False
        return None

    def count_beside(self, node):
        return node.kind == 'tb-person'

    def copy_text(self, node):
        if node.kind == 'tb-person':
            return node.data['address'] or None
        if node.kind == 'tb-body':
            return copied(node.name)
        return node.name

    def unread(self, node):
        k, d = node.kind, node.data
        if k == 'tb-folder':
            return bool(d.get('unreadMessages'))
        if k in ('tb-thread', 'tb-group'):
            return bool(d['unread'])
        return k == 'tb-message' and d.get('read') is False

    def colour(self, node):
        if node.kind == 'tb-subject':
            return 'path', True
        if node.kind in ('tb-when', 'tb-lines'):
            return 'stub', False
        if node.kind == 'tb-attached':
            return 'path', True
        return ('dir', True) if self.unread(node) else None

    def chip(self, node):
        """People in their colours, as in the native Emails and chats: their own, from their address;
        attached files as blocks of their own."""
        if node.kind == 'tb-attachment' and not node.data.get('inline'):
            return ATTACHED
        if node.kind == 'tb-person' and node.data['address']:
            address = node.data['address'].casefold()
            if address in self.mine:
                return 'home_fg', 'home_bg'                     # you
            return 'person_fg', f'person{zlib.crc32(address.encode()) % fc.PEOPLE}'
        return None

    def mark(self, node):
        k, d = node.kind, node.data
        if k in ('tb-thread', 'tb-message', 'tb-group'):
            if self.unread(node):
                return 'git_dirty', '●', '*'
            if d.get('flagged'):
                return 'git_staged', '★', '!'
        return None

    def mark_before(self, node):
        return node.kind in ('tb-group', 'tb-thread', 'tb-message')

    def beacon(self, node):
        """Unread mail newer than BEACON_DAYS as a beacon, as the native Emails show it."""
        if node.kind == 'tb-thread' and node.data['unread']:
            came = max(m['stamp'] for m in node.data['messages'] if m.get('read') is False)
        elif node.kind == 'tb-message' and node.data.get('read') is False:
            came = node.data['stamp']
        else:
            return None
        return came if came and time.time() - came < BEACON_DAYS * 86400 else None

    def dim(self, node):
        return 0.8 if node.kind == 'tb-folder' and not node.data.get('totalMessages') else 1.0

    def width(self, node):
        return BODY_WIDTH if node.kind in ('tb-body', 'tb-note') else None

    def reference(self, node):
        at = node
        while at.parent and at.kind not in ('tb-message', 'tb-thread'):
            at = at.parent
        if at.kind not in ('tb-message', 'tb-thread'):
            at = node
        content = [n for n in at.load() if n.kind in CONTENT]
        return dict(root=at, nodes=content, width=BODY_WIDTH, labels={})

    def bar(self, node, plain):
        names, n = [], node
        while n.parent:
            names.append(who(n.data.get('author')) if n.kind == 'tb-message' else n.name.split('\n')[0][:40])
            n = n.parent
        names.reverse()
        segs = [('mail', 'home_fg', 'home_bg', False)] + [
            (t, 'cwd_fg' if i == len(names) - 1 else 'seg_fg', 'seg_bg', i == len(names) - 1)
            for i, t in enumerate(names)]
        tail = []
        f = node
        while f is not None and f.kind != 'tb-folder':
            f = f.parent
        if f is not None:
            d = f.data
            tail.append((f"{d['unreadMessages']} unread" if d.get('unreadMessages') else f"{d.get('totalMessages', 0)} messages",
                         'repo_dirty_fg' if d.get('unreadMessages') else 'repo_clean_fg',
                         'repo_dirty_bg' if d.get('unreadMessages') else 'repo_clean_bg', False))
        if node.kind in ('tb-folder', 'tb-group'):
            tail.append((f'by {self.grouping}' if self.grouping != 'all' else 'not grouped', 'bg', 'filter', False))
        return segs, tail + [('via Thunderbird', 'seg_fg', 'seg_bg', False)]

    def busy(self):
        with self.lock:
            return bool(self.pending or self.working)

    def close(self):
        self.closed = True
        self.jobs.put(None)
        if self.client:
            self.client.close()
        self.worker.join(timeout=3)


class NotConnected(fc.Provider):
    """Emails, before twigs thunderbird connect: what to run."""
    def root(self):
        return fc.Node('/thunderbird', 'Emails', True, source=self, kind='tb-root')

    def children(self, node):
        if node.kind != 'tb-root':
            return []
        return [fc.Node(node.path+'/connect', 'Thunderbird is ready, but no accounts are connected yet\n'
                        'Run: twigs thunderbird connect', False, node, 'tb-note')]

    def width(self, node):
        return BODY_WIDTH
