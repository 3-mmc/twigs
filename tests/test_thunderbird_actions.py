"""What Thunderbird mail does when you ask: flags, moving, deleting, writing, and its pictures.
All mail and identities here are synthetic; the bridge is a fixture."""
import os
from unittest.mock import patch

from twigs import core as fc
from twigs.apps.msg import compose
from twigs.apps.thunderbird.canvas import Thunderbird
from tests.test_images import png

ACCOUNT = dict(id='a2', name='Mail', identities=[dict(email='me@example.test', name='Me', isDefault=True)])
INBOX = dict(name='INBOX', path='imap://me/INBOX', type='inbox', depth=0, accountId='a2', unreadMessages=2, totalMessages=3)
ARCHIVE = dict(name='Archive', path='imap://me/Archive', type='archive', depth=0, accountId='a2', totalMessages=0)
TRASH = dict(name='Trash', path='imap://me/Trash', type='trash', depth=0, accountId='a2', totalMessages=0)
WORK = dict(name='Work', path='imap://me/Work', type='folder', depth=0, accountId='a2', totalMessages=0)
WORKSHOP = dict(name='Workshop', path='imap://me/Workshop', type='folder', depth=0, accountId='a2', totalMessages=0)


def message(ident, read, thread, author='Ann <ann@example.test>', subject='Kiln', date='2026-11-10T10:00:00Z'):
    return dict(id=ident, threadId=thread, subject=subject, author=author, recipients='me@example.test, bo@example.test',
                ccList='cy@example.test', date=date, folderPath=INBOX['path'], read=read, flagged=False)


class Bridge:
    """Thunderbird, as far as these tests need: what's asked of it is kept in `calls`."""
    refuse = ()         # tools that fail, and with what

    def __init__(self, config):
        self.closed, self.calls = False, []

    def call(self, name, args=None):
        self.calls.append((name, args))
        if name in dict(self.refuse) and not (args or {}).get('saveAsDraft'):
            raise OSError(dict(self.refuse)[name])
        if name == 'listAccounts':
            return [ACCOUNT]
        if name == 'listFolders':
            return [dict(f) for f in (INBOX, ARCHIVE, TRASH, WORK, WORKSHOP)]
        if name == 'searchMessages':
            return dict(messages=[message('m1', False, 1), message('m2', False, 1, date='2026-11-10T11:00:00Z'),
                                  message('m3', True, 2, subject='Pass')], offset=0, limit=200, hasMore=False)
        if name == 'getMessage':
            return dict(Bridge.message, id=args['messageId'])
        return dict(success=True)

    def close(self):
        self.closed = True


Bridge.message = dict(body='Hello', attachments=[])


class Canvas:
    """What an action asks of the canvas: what it says, asks, and runs outside it."""
    def __init__(self):
        self.said, self.asked = None, None

    def say(self, text):
        self.said = text

    def ask(self, prompt, done, text='', choices=None, changed=None):
        self.asked = (prompt, done)

    def outside(self, fn):
        fn()


def setup(config=None):
    p = Thunderbird(dict(accounts=['a2'], **(config or {})), client_factory=Bridge, grouping='all')
    root = p.root()
    account = load(p, root)[0]
    inbox = next(n for n in load(p, account) if n.data['path'] == INBOX['path'])
    load(p, inbox)
    return p, account, inbox


def load(p, node):
    node.load()
    p.wait()
    node.kids = None
    return node.load()


def threads(p, inbox):
    inbox.kids = None
    return {n.name: n for n in inbox.load() if n.kind == 'tb-thread'}


def tool(p, name):
    return [a for n, a in p.client.calls if n == name]


def statuses(p):
    return [s for s in p.signals() if s[0] == 'status']


def test_k_marks_a_thread_read_at_once_then_in_thunderbird():
    p, account, inbox = setup()
    try:
        kiln = threads(p, inbox)['Kiln']
        assert dict((k, h) for k, _, h in p.actions(kiln))['\x0b'] == '⌃K mark read'
        assert p.act(kiln, 'read', Canvas()) is None            # stays on the canvas
        assert inbox.data['unreadMessages'] == 0 and not threads(p, inbox)['Kiln'].data['unread']
        p.wait()
        assert tool(p, 'updateMessage') == [dict(messageIds=['m1', 'm2'], folderPath=INBOX['path'], read=True)]
        assert ('status', 'tb-update', 'marked read', 'ok') in statuses(p)
        kiln = threads(p, inbox)['Kiln']
        assert dict((k, h) for k, _, h in p.actions(kiln))['\x0b'] == '⌃K mark unread'
        p.act(kiln, 'flag', Canvas())
        p.wait()
        assert tool(p, 'updateMessage')[-1]['flagged'] is True and threads(p, inbox)['Kiln'].data['flagged']
    finally:
        p.close()


def test_going_into_a_message_reads_it_unless_mark_read_is_off():
    for setting, read in ((None, True), (dict(mark_read=False), False)):
        p, account, inbox = setup(setting)
        try:
            kiln = threads(p, inbox)['Kiln']
            first = load(p, kiln)[0]
            subject = load(p, first)[0]
            p.focus(subject)
            p.wait()
            assert bool(tool(p, 'updateMessage')) == read
            if read:
                assert tool(p, 'updateMessage')[0] == dict(messageIds=['m1'], folderPath=INBOX['path'], read=True)
        finally:
            p.close()


def test_trash_archive_and_move_take_it_out_of_the_folder():
    p, account, inbox = setup()
    try:
        c = Canvas()
        p.act(threads(p, inbox)['Pass'], 'archive', c)
        assert 'Pass' not in threads(p, inbox) and inbox.data['totalMessages'] == 2
        p.wait()
        assert tool(p, 'updateMessage')[-1] == dict(messageIds=['m3'], folderPath=INBOX['path'], moveTo=ARCHIVE['path'])
        kiln = threads(p, inbox)['Kiln']
        p.act(kiln, 'move', c)
        prompt, done = c.asked
        done('work')                                            # Work, exactly, though Workshop has it too
        assert not threads(p, inbox)
        p.wait()
        assert tool(p, 'updateMessage')[-1]['moveTo'] == WORK['path']
    finally:
        p.close()
    p, account, inbox = setup()
    try:
        c = Canvas()
        p.act(threads(p, inbox)['Kiln'], 'move', c)
        c.asked[1]('ork')                                       # Work or Workshop: which?
        assert c.said == '2 folders match: Work, Workshop' and 'Kiln' in threads(p, inbox)
        p.act(threads(p, inbox)['Kiln'], 'delete', c)
        p.wait()
        assert tool(p, 'updateMessage')[-1] == dict(messageIds=['m1', 'm2'], folderPath=INBOX['path'], trash=True)
    finally:
        p.close()


def test_in_the_trash_delete_is_for_good_and_asks_first():
    p, account, inbox = setup()
    try:
        trash = next(n for n in load(p, account) if n.data['path'] == TRASH['path'])
        node = fc.Node(trash.path + '/t', 'Kiln', True, trash, 'tb-thread',
                       data=dict(messages=[dict(message('m9', True, 9), folderPath=TRASH['path'])], count=1,
                                 unread=False, flagged=False))
        assert dict((k, h) for k, _, h in p.actions(node))['\x04'] == '⌃D delete'
        c = Canvas()
        p.act(node, 'delete', c)
        assert c.asked[0].startswith('delete it for good?') and not tool(p, 'deleteMessages')
        c.asked[1]('y')
        p.wait()
        assert tool(p, 'deleteMessages') == [dict(messageIds=['m9'], folderPath=TRASH['path'])]
    finally:
        p.close()


def write(p, node, kind, text, answer='y', edit=None):
    """Write `kind` from `node` in a pretend editor (adding `text` under the headers, or edit()
    changing the draft), then answer the question after it."""
    c, drafts = Canvas(), []

    def editor(cmd):
        path = cmd[-1]
        drafts.append(path)
        headers, attach, body = compose.parse(path)
        if edit:
            edit(headers)
        compose.write(path, headers, attach, body + text)
    with patch.object(compose, 'edit_command', lambda path: ['editor', path]), \
            patch('twigs.core.terminal', editor):
        p.act(node, kind, c)
    c.asked[1](answer)
    p.wait()
    return c, drafts[0]


def test_reply_sends_only_what_you_wrote_and_what_you_changed():
    p, account, inbox = setup()
    try:
        kiln = threads(p, inbox)['Kiln']
        c, path = write(p, kiln, 'reply', 'Thursday works.')
        sent = tool(p, 'replyToMessage')[0]
        assert sent == dict(messageId='m2', folderPath=INBOX['path'], body='Thursday works.\n', isHtml=False,
                            skipReview=True, replyAll=False, **{'from': 'me@example.test'})   # the newest; To as it was
        assert not os.path.exists(path)
        assert ('status', 'tb-send', 'sent to ann@example.test', 'ok') in statuses(p)
        c, path = write(p, kiln, 'reply-all', 'All of you.', edit=lambda h: h.update(Bcc='dee@example.test'))
        sent = tool(p, 'replyToMessage')[-1]
        assert sent['replyAll'] and sent['bcc'] == 'dee@example.test' and 'cc' not in sent
    finally:
        p.close()


def test_reply_all_leaves_you_out_and_new_mail_needs_someone():
    p, account, inbox = setup()
    try:
        heads = []
        write(p, threads(p, inbox)['Kiln'], 'reply-all', '', answer='q', edit=lambda h: heads.append(dict(h)))
        assert heads[0]['To'] == 'Ann <ann@example.test>' and heads[0]['Cc'] == 'bo@example.test, cy@example.test'
        assert heads[0]['Replying-To'] == 'Ann: Kiln' and not tool(p, 'replyToMessage')
        c, path = write(p, account, 'new', 'Hi.')
        assert c.asked[0].startswith('nobody to send it to:') and os.path.exists(path)     # asked again
        c.asked[1]('q')
        assert not os.path.exists(path)
        c, path = write(p, account, 'new', 'Hi.', edit=lambda h: h.update(To='bo@example.test', Subject='Hello'))
        assert tool(p, 'sendMail') == [dict(to='bo@example.test', cc='', bcc='', subject='Hello', body='Hi.\n',
                                            isHtml=False, skipReview=True, **{'from': 'me@example.test'})]
    finally:
        p.close()


def test_when_thunderbird_reviews_sending_it_goes_to_drafts_instead():
    p, account, inbox = setup()
    Bridge.refuse = (('replyToMessage', 'User preference blocks skipReview. Retry with skipReview: false'),)
    try:
        kiln = threads(p, inbox)['Kiln']
        c, path = write(p, kiln, 'reply', 'Later.')
        saved = tool(p, 'replyToMessage')[-1]
        assert saved['saveAsDraft'] and 'skipReview' not in saved and not os.path.exists(path)
        assert any(s[3] == 'failed' and 'in Drafts' in s[2] for s in statuses(p))
    finally:
        Bridge.refuse = ()
        p.close()


def test_a_message_shows_its_pictures_and_opens_what_is_attached(tmp_path):
    big, tiny, doc = tmp_path / 'photo.png', tmp_path / 'pixel.png', tmp_path / 'notes.pdf'
    big.write_bytes(png(64, 48))
    tiny.write_bytes(png(1, 1))
    doc.write_bytes(b'%PDF-1.4\n')
    Bridge.message = dict(body='Look', attachments=[
        dict(name='photo.png', contentType='image/png', isInline=True, filePath=str(big)),
        dict(name='pixel', contentType='image/png', isInline=True, filePath=str(tiny)),
        dict(name='notes.pdf', contentType='application/pdf', isInline=False, size=9, filePath=str(doc))])
    p, account, inbox = setup()
    try:
        pass_ = threads(p, inbox)['Pass']
        parts = load(p, pass_)
        # the picture in the body after the text (not the pixel), then what's attached, set apart
        assert [(n.kind, n.name) for n in parts if n.kind in ('tb-attachment', 'tb-attached')] == [
            ('tb-attachment', '📷 photo.png'), ('tb-attached', '📎 1 attached'), ('tb-attachment', 'PDF  notes.pdf')]
        picture, heading, pdf = [n for n in parts if n.kind in ('tb-attachment', 'tb-attached')]
        assert p.picture(picture) == str(big) and p.picture(pdf) is None
        assert p.chip(pdf) and not p.chip(picture) and p.count(heading)[0] == '9 B'
        body = next(n for n in parts if n.kind == 'tb-body')
        with patch.object(fc, 'GRAPHICS', None):        # no pictures in the terminal: the pane has them
            assert p.pane_for(body) and [x.path for x in p.preview(body)] == [str(big)]
        with patch.object(fc, 'GRAPHICS', 'kitty'):     # else they're on the canvas
            assert not p.pane_for(body)
        acts = {k: n for k, n, _ in p.actions(pass_)}
        assert acts['\x0f'] == 'open' and acts['\x13'] == 'save'
        c, opened = Canvas(), []
        with patch('twigs.apps.msg.files.open_file', opened.append):
            p.act(pass_, 'open', c)
        assert opened == [str(doc)]
        with patch('twigs.apps.msg.files.downloads', lambda: str(tmp_path / 'dl')):
            os.makedirs(tmp_path / 'dl')
            p.act(pdf, 'save-one', c)
        assert os.listdir(tmp_path / 'dl') == ['notes.pdf']
        assert tool(p, 'getMessage')[0]['saveAttachments'] is True
    finally:
        Bridge.message = dict(body='Hello', attachments=[])
        p.close()


def test_folder_keys_stay_on_the_canvas():
    p, account, inbox = setup()
    try:
        acts = {k: n for k, n, _ in p.actions(inbox)}
        assert acts == {'\x0e': 'new', '\x12': 'refresh', '\x06': 'search', '\x0c': 'group'}
        c = Canvas()
        assert p.act(inbox, 'group', c) is None and p.act(inbox, 'refresh', c) is None
    finally:
        p.close()


def test_ctrl_r_fetches_a_message_again_and_ctrl_n_on_it_replies():
    p, account, inbox = setup()
    try:
        pass_ = threads(p, inbox)['Pass']                       # a thread of one: it reads as its message
        acts = {k: n for k, n, _ in p.actions(pass_)}
        assert acts['\x0e'] == 'reply' and acts['\x12'] == 'reload' and '\x0c' not in acts
        assert acts['ctrl-.'] == acts['\x14'] == 'move' and '\x16' not in acts
        Bridge.refuse = (('getMessage', 'Thunderbird timed out'),)
        notes = load(p, pass_)
        assert notes[0].kind == 'tb-note' and notes[0].name.endswith('Ctrl+R to retry')
        assert {k: n for k, n, _ in p.actions(notes[0])}['\x12'] == 'reload'
        Bridge.refuse = ()
        c = Canvas()
        p.act(notes[0], 'reload', c)                            # from the error beside it, as from the message
        p.wait()
        pass_.kids = None
        assert c.said == 'Fetching the message again'
        assert [n.kind for n in pass_.load()][0] == 'tb-subject'
    finally:
        Bridge.refuse = ()
        p.close()


def test_an_account_renamed_in_twigs_keeps_the_name_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    from twigs.apps.thunderbird import settings
    settings.save(dict(accounts=['a2'], mark_read=False))
    p, account, inbox = setup(dict(mark_read=False))
    try:
        assert account.name == 'Mail'
        assert {k: n for k, n, _ in p.actions(account)}['\x05'] == 'rename'
        c = Canvas()
        p.act(account, 'rename', c)
        prompt, done = c.asked
        done('  Work   mail ')
        assert c.said == 'renamed to Work   mail'
        assert settings.load() == dict(accounts=['a2'], mark_read=False, names={'a2': 'Work mail'})
        assert load(p, p.root())[0].name == 'Work mail'
        done('')                                                # empty: Thunderbird's own name again
        assert settings.load() == dict(accounts=['a2'], mark_read=False)
        assert load(p, p.root())[0].name == 'Mail'
    finally:
        p.close()
