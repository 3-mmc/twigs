"""Settings, on the canvas: what twigs keeps in ~/.config/twigs, changed where you see it rather
than in its files. A section of `twigs` (and `twigs settings` alone).

    Twigs           which twigs `twigs` shows (Enter turns one on or off; from its next start)
    Thunderbird     marking read, accounts also set up elsewhere, and each account: shown or
                    not (Enter), and its name in twigs (⌃E)

A change is saved at once, and the open sections take it up when they're on the same canvas."""
import threading

from twigs import core as fc
from twigs.kit import apps
from twigs.kit.text import plain

ROOT = '/settings'


class Settings(fc.Provider):
    settled = True          # nothing to load: the opening screen doesn't wait on it
    def __init__(self, providers=(), client_factory=None):
        """`providers`: the sections open beside this one, to tell of a change (Emails)."""
        self.providers = list(providers)
        self.client_factory = client_factory
        self.accounts, self.error = None, None      # Thunderbird's accounts, all of them, once asked
        self.asking = False
        self.lock = threading.Lock()

    def root(self):
        return fc.Node(ROOT, 'Settings', True, source=self, kind='set-root')

    def live(self):
        """The open Thunderbird section, or None."""
        from twigs.apps.thunderbird.canvas import Thunderbird
        return next((p for p in self.providers if isinstance(p, Thunderbird)), None)

    # -- what there is
    def children(self, node):
        k, out = node.kind, []

        def add(suffix, label, kind, data=None, branch=False):
            out.append(fc.Node(node.path + '/' + suffix, label, branch, node, kind, data=data))
        if k == 'set-root':
            add('twigs', 'Twigs', 'set-twigs', branch=True)
            tb = self.thunderbird()
            if tb is not None:
                add('thunderbird', 'Thunderbird', 'set-tb', branch=True)
        elif k == 'set-twigs':
            have = set(apps.installed())
            for name, app in apps.every().items():
                add(name, f"{name}: {'on' if name in have else 'off'}\n{app['summary']}", 'set-app', name)
            add('note', 'Turning a twig on or off shows from the next start of twigs', 'set-note')
        elif k == 'set-tb':
            tb = self.thunderbird() or {}
            add('mark_read', f"Mark a message read when it's opened: {on(tb.get('mark_read', True))}",
                'set-toggle', 'mark_read')
            add('include_existing', f"Show accounts also set up in msg: {on(tb.get('include_existing', False))}",
                'set-toggle', 'include_existing')
            add('accounts', 'Accounts', 'set-tb-accounts', branch=True)
        elif k == 'set-tb-accounts':
            with self.lock:
                accounts, error = self.accounts, self.error
            if accounts is None and not error:
                self.ask_accounts()
                add('loading', 'Asking Thunderbird for its accounts…', 'set-note')
            if error:
                add('error', error + '\nCtrl+R to retry', 'set-note')
            tb = self.thunderbird() or {}
            names = tb.get('names', {})
            for a in accounts or []:
                address = next((i['email'] for i in a.get('identities', []) if i.get('email')), '')
                shown = a['id'] in tb.get('accounts', [])
                name = names.get(a['id']) or a['name']
                extra = f'\n{address}' if address and address != name else ''
                add('a' + a['id'], plain(f"{name}: {'shown' if shown else 'hidden'}{extra}"), 'set-tb-account', a)
        return out

    def dim(self, node):
        if node.kind == 'set-note':
            return 0.6
        if node.kind == 'set-tb-account' and node.data['id'] not in (self.thunderbird() or {}).get('accounts', []):
            return 0.6
        if node.kind == 'set-app' and node.data not in apps.installed():
            return 0.6
        return 1.0

    def width(self, node):
        return 60 if node.kind.startswith('set-') and not node.is_dir else None

    @staticmethod
    def thunderbird():
        from twigs.apps.thunderbird import settings
        try:
            return settings.load()
        except ValueError:
            return None

    def ask_accounts(self):
        """All of Thunderbird's accounts, not just the shown ones: from the bridge, on a thread."""
        with self.lock:
            if self.asking:
                return
            self.asking = True

        def run():
            from twigs.apps.thunderbird.connection import Client
            client = None
            try:
                client = (self.client_factory or Client)(self.thunderbird() or {})
                accounts = [a for a in client.call('listAccounts') if a.get('type') != 'none']
                with self.lock:
                    self.accounts, self.error = accounts, None
            except (OSError, ValueError, KeyError, TypeError) as e:
                from twigs.apps.thunderbird.canvas import Thunderbird
                with self.lock:
                    self.error = Thunderbird.explain(e)
            finally:
                if client is not None:
                    client.close()
                with self.lock:
                    self.asking = False
                self.invalidate(ROOT + '/thunderbird/accounts', deep=True)
        threading.Thread(target=run, daemon=True).start()

    # -- changing it
    def choose(self, node):
        if node.is_dir:
            return None
        k = node.kind
        if k == 'set-app':
            have = apps.installed()
            apps.keep([n for n in have if n != node.data] if node.data in have else have + [node.data])
            self.invalidate(node.parent.path)
        elif k == 'set-toggle':
            self.toggle(node.data)
            self.invalidate(node.parent.path)
        elif k == 'set-tb-account':
            self.show(node.data['id'])
            self.invalidate(node.parent.path)
        return fc.STAY

    def actions(self, node):
        k = node.kind
        if k == 'set-tb-account':
            return [('\x05', 'rename', '⌃E rename')]
        if k in ('set-tb-accounts',) or (k == 'set-note' and node.parent.kind == 'set-tb-accounts'):
            return [('\x12', 'refresh', '⌃R ask again')]
        return []

    def act(self, node, name, canvas):
        if name == 'refresh':
            with self.lock:
                self.accounts, self.error = None, None
            self.invalidate(ROOT + '/thunderbird/accounts', deep=True)
        elif name == 'rename' and node.kind == 'set-tb-account':
            names = (self.thunderbird() or {}).get('names', {})
            canvas.ask('Name this account (empty: as Thunderbird names it)',
                       lambda text: self.rename_tb(canvas, node, text), text=names.get(node.data['id']) or node.data['name'])
        return fc.STAY

    def toggle(self, key):
        from twigs.apps.thunderbird import settings
        default = dict(mark_read=True, include_existing=False)[key]
        data = settings.update(lambda d: d.__setitem__(key, not d.get(key, default)))
        p = self.live()
        if p is not None:
            p.config[key] = data[key]
            if key == 'include_existing':
                p.forget()

    def show(self, account):
        from twigs.apps.thunderbird import settings

        def flip(d):
            d['accounts'] = [a for a in d['accounts'] if a != account] if account in d['accounts'] \
                else d['accounts'] + [account]
        data = settings.update(flip)
        p = self.live()
        if p is not None:
            p.config['accounts'] = list(data['accounts'])
            p.forget()

    def rename_tb(self, canvas, node, text):
        from twigs.apps.thunderbird import settings
        try:
            names = settings.rename(node.data['id'], plain(text))
        except (OSError, ValueError) as e:
            canvas.say(f'could not rename: {e}')
            return None
        p = self.live()
        if p is not None:
            p.config['names'] = names
            p.invalidate(p.root().path, deep=True)
        self.invalidate(node.parent.path)
        canvas.say(f'renamed to {names[node.data["id"]]}' if node.data['id'] in names else 'named as in Thunderbird again')
        return None


def on(value):
    return 'on' if value else 'off'


def main(argv=None):
    """`twigs settings`: this section alone."""
    from twigs.__main__ import LOOK
    fc.pick('', provider=Settings(), **LOOK)
