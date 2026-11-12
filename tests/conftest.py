"""What every test shares."""
import pytest


@pytest.fixture(autouse=True)
def no_desktop_notifications(monkeypatch):
    """Tests never reach the desktop: a snooze test once told whoever ran it "Back from snooze /
    Your pass" on every run. Every notification goes through twigs.kit.notify.notify; what a test
    would have shown is kept here instead."""
    from twigs.kit import notify
    shown = []
    monkeypatch.setattr(notify, 'notify', lambda title, body: shown.append((title, body)))
    return shown


@pytest.fixture(autouse=True)
def own_state(monkeypatch, tmp_path):
    """Tests keep their state (pins, say) to themselves, never in whoever runs them's."""
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
