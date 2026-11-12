"""Settings on the canvas: saved at once, and taken up by the sections open beside it. Synthetic
accounts; Thunderbird's bridge is the fixture the actions tests use."""
import json
import time

import pytest

from twigs import core as fc
from twigs.kit import apps
from twigs.kit.settings import Settings
from twigs.kit.text import imap_utf7
from twigs.apps.thunderbird import settings as tb
from twigs.apps.thunderbird.canvas import Thunderbird, folders
from tests.test_thunderbird_actions import ACCOUNT, Bridge, Canvas

OTHER = dict(id='a3', name='uni@example.test', identities=[dict(email='uni@example.test')])


class TwoAccounts(Bridge):
    def call(self, name, args=None):
        return [ACCOUNT, OTHER, dict(id='local', name='Local Folders', type='none')] if name == 'listAccounts' \
            else super().call(name, args)


@pytest.mark.parametrize('raw, shown', [
    ('&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-', 'Отправленные'), ('&BCEEPwQwBDw-', 'Спам'),
    ('Tom &- Jerry', 'Tom & Jerry'), ('R&D', 'R&D'), ('&Jjo-!', '☺!'), ('&AB-', '&AB-'), ('INBOX', 'INBOX')])
def test_imap_folder_names_read_as_unicode_and_others_stay_as_they_are(raw, shown):
    assert imap_utf7(raw) == shown


def test_a_folder_keeps_the_servers_path_and_shows_its_name():
    rows = [dict(name='INBOX', path='imap://me/INBOX', type='inbox', depth=0),
            dict(name='&BCEEPwQwBDw-', path='imap://me/&BCEEPwQwBDw-', type='folder', depth=1)]
    spam = folders(rows)[-1]
    assert spam['rel'] == 'INBOX/Спам' and spam['name'] == 'Спам' and spam['path'] == 'imap://me/&BCEEPwQwBDw-'


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    monkeypatch.setattr(apps, 'INSTALLED', str(tmp_path / 'twigs' / 'installed'))
    monkeypatch.setattr(apps, 'CONFIG', str(tmp_path / 'twigs'))
    return tmp_path


def wait_for(fn):
    for _ in range(100):
        if fn():
            return
        time.sleep(0.02)
    raise AssertionError('never happened')


def kids(node, kind=None):
    node.kids = None
    return {n.path.rsplit('/', 1)[1]: n for n in node.load() if kind is None or n.kind == kind}


def test_twigs_turn_on_and_off_from_the_list(home):
    s = Settings()
    group = kids(s.root())['twigs']
    assert set(kids(group, 'set-app')) == set(apps.BUILT_IN)
    assert s.choose(kids(group)['cal']) is fc.STAY
    assert 'cal' not in apps.installed()
    assert kids(group)['cal'].name.startswith('cal: off')
    s.choose(kids(group)['cal'])
    assert 'cal' in apps.installed()


def test_thunderbird_choices_are_saved_and_the_open_emails_take_them_up(home):
    tb.save(dict(accounts=['a2']))
    emails = Thunderbird(dict(accounts=['a2']), client_factory=TwoAccounts, grouping='all')
    s = Settings([emails], client_factory=TwoAccounts)
    try:
        section = kids(s.root())['thunderbird']
        toggles = kids(section, 'set-toggle')
        assert toggles['mark_read'].name.endswith(': on')
        s.choose(toggles['mark_read'])
        assert tb.load()['mark_read'] is False and emails.config['mark_read'] is False
        assert kids(section)['mark_read'].name.endswith(': off')

        accounts = kids(section)['accounts']
        assert next(iter(kids(accounts).values())).kind == 'set-note'           # asking Thunderbird
        wait_for(lambda: s.accounts is not None)
        listed = kids(accounts, 'set-tb-account')
        assert set(listed) == {'aa2', 'aa3'}                                    # Local Folders isn't an account to show
        assert listed['aa2'].name == 'Mail: shown\nme@example.test'
        assert listed['aa3'].name == 'uni@example.test: hidden'

        s.choose(listed['aa3'])
        assert tb.load()['accounts'] == ['a2', 'a3'] and emails.config['accounts'] == ['a2', 'a3']
        emails.root().load(); emails.wait()
        assert [n.data['id'] for n in emails.root().load()] == ['a2', 'a3']

        c = Canvas()
        s.act(listed['aa3'], 'rename', c)
        prompt, done = c.asked
        done('Uni')
        assert tb.load()['names'] == {'a3': 'Uni'} and c.said == 'renamed to Uni'
        assert [n.name for n in emails.root().load()] == ['Mail', 'Uni']
        assert kids(accounts)['aa3'].name == 'Uni: shown\nuni@example.test'
    finally:
        emails.close()

