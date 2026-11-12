"""Files as a section of twigs: home, then the rest of the disk; pictures, PDFs and videos in the pane."""
import os, shutil, tempfile, threading
from unittest.mock import patch

import pytest

from twigs import core as fc
from twigs.apps.files.hub import Files
from twigs.kit import together
from tests.test_images import png


@pytest.fixture
def home(tmp_path):
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs' / 'photo.png').write_bytes(png(40, 30))
    (tmp_path / 'docs' / 'notes.txt').write_text('hello')
    (tmp_path / 'docs' / 'paper.pdf').write_bytes(b'%PDF-1.4\n')
    return tmp_path


def files(home):
    f = Files()
    f.home = str(home)
    return f


def test_a_section_named_files_holding_home_then_the_whole_disk(home):
    f = files(home)
    c = fc.Canvas('', fc.options(provider=together.Together([('Files', f)]), backdrop='off', ease=1, glyphs='plain'))
    section = c.root.load()[0]
    assert (section.label(), section.path) == ('Files', str(home))
    kids = section.load()
    assert [k.label() for k in kids] == ['docs/', '/ (everything)']
    assert f.label(kids[0].load()[0]) == 'notes.txt'             # below, files as the file canvas has them
    assert f.locate('') == ([], '')                              # twigs doesn't open on it


def test_enter_opens_a_file_in_its_app_and_stays(home):
    f = files(home)
    node = fc.Node(str(home / 'docs' / 'notes.txt'), 'notes.txt', False, None, 'fi', source=f)
    with patch('twigs.apps.msg.files.open_file') as opened:
        assert f.choose(node) is fc.STAY
    opened.assert_called_once_with(node.path)
    folder = fc.Node(str(home / 'docs'), 'docs', True, None, 'di', source=f)
    assert f.choose(folder) is None


def test_pictures_show_themselves_and_pdfs_their_first_page(home, tmp_path_factory):
    f = files(home)
    node = lambda name: fc.Node(str(home / 'docs' / name), name, False, None, 'fi', source=f)
    got = f.preview(node('photo.png'))
    assert got[0].startswith('40 × 30 · ') and isinstance(got[2], fc.Picture)
    assert f.preview(node('notes.txt')) == 'hello'
    thumbs = fc.Thumbnails()
    thumbs.folder = str(tmp_path_factory.mktemp('thumbs'))
    made, done = threading.Event(), threading.Event()

    def pdftoppm(cmd, **kw):                                     # a page drawn: the PNG it writes
        with open(cmd[-1] + '.png', 'wb') as out:
            out.write(png(20, 28))
        made.set()
    with patch.object(fc, 'THUMBNAILS', thumbs), patch.object(fc.shutil, 'which', lambda name: '/bin/' + name), \
            patch.object(fc.subprocess, 'run', pdftoppm), patch.object(f, 'invalidate', lambda path: done.set()):
        assert f.preview(node('paper.pdf'))[2] == '(making a preview…)'
        assert done.wait(5) and made.is_set()                    # made in the background, then drawn again
        got = f.preview(node('paper.pdf'))
    assert isinstance(got[2], fc.Picture) and got[2].path.startswith(thumbs.folder)
