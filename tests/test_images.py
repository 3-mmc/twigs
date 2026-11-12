"""Pictures drawn by the terminal itself (kitty's graphics protocol, Unicode placeholders)."""
import base64, os, re, struct, tempfile, unittest, zlib
from unittest.mock import patch

from twigs import core as fc


def png(width, height):
    """A plain RGB PNG, made with the standard library only."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = b''.join(b'\0' + b'\x80\x40\x20' * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


class Detect(unittest.TestCase):
    def test_kitty_and_ghostty_draw_pictures_others_get_blocks(self):
        self.assertEqual(fc.graphics_terminal({'TERM': 'xterm-kitty'}), 'kitty')
        self.assertEqual(fc.graphics_terminal({'TERM': 'xterm-256color', 'KITTY_WINDOW_ID': '3'}), 'kitty')
        self.assertEqual(fc.graphics_terminal({'TERM': 'xterm-ghostty'}), 'kitty')
        self.assertIsNone(fc.graphics_terminal({'TERM': 'xterm-256color'}))
        self.assertIsNone(fc.graphics_terminal({'TERM': 'xterm-kitty', 'TWIGS_IMAGES': 'blocks'}))
        self.assertEqual(fc.graphics_terminal({'TERM': 'xterm-256color', 'TWIGS_IMAGES': 'kitty'}), 'kitty')

    def test_through_tmux_only_when_it_lets_them_pass(self):
        env = {'TERM': 'tmux-256color', 'KITTY_WINDOW_ID': '1', 'TMUX': '/tmp/tmux-1/default,1,0'}
        for said, want in (('on\n', 'kitty'), ('off\n', None)):
            with patch.object(fc.subprocess, 'run') as run:
                run.return_value.stdout = said
                self.assertEqual(fc.graphics_terminal(env), want)

    def test_the_diacritics_are_kittys_table(self):
        self.assertEqual(len(fc.DIACRITICS), 297)
        self.assertEqual((fc.DIACRITICS[0], fc.DIACRITICS[1], fc.DIACRITICS[-1]), ('̅', '̍', '\U0001d244'))


class Placing(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'p.png')
        with open(self.path, 'wb') as f:
            f.write(png(100, 50))
        self.images = fc.Images()
        patcher = patch.object(fc, 'CELL', (10.0, 20.0))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sizes_from_headers(self):
        self.assertEqual(fc.image_size(self.path), (100, 50))
        gif = os.path.join(self.dir, 'g.gif')
        with open(gif, 'wb') as f:
            f.write(b'GIF89a' + struct.pack('<HH', 7, 9) + b'\0' * 8)
        self.assertEqual(fc.image_size(gif), (7, 9))
        jpeg = os.path.join(self.dir, 'j.jpg')
        with open(jpeg, 'wb') as f:      # SOI, an APP0 to step over, then SOF0 with 60 × 40
            f.write(b'\xff\xd8' + b'\xff\xe0' + struct.pack('>H', 4) + b'JF' +
                    b'\xff\xc0' + struct.pack('>HBHH', 11, 8, 40, 60) + b'\0' * 6)
        self.assertEqual(fc.image_size(jpeg), (60, 40))
        self.assertIsNone(fc.image_size(os.path.join(self.dir, 'none')))

    def test_a_picture_is_placeholder_cells_its_size_keeping_its_shape(self):
        lines = self.images.place(self.path, 40, 20)
        self.assertEqual(len(lines), 3)                  # 100 × 50 px in 10 × 20 cells: 10 × 2.5, rounded up
        self.assertTrue(all(re.sub(r'\x1b\[[0-9;]*m', '', l).count(fc.PLACEHOLDER) == 10 for l in lines))
        first = re.sub(r'\x1b\[[0-9;]*m', '', lines[1])
        self.assertEqual(first[:3], fc.PLACEHOLDER + fc.DIACRITICS[1] + fc.DIACRITICS[0])   # row 1, column 0
        narrow = self.images.place(self.path, 4, 20)     # narrower: fewer columns, rows to match
        self.assertEqual((len(narrow), re.sub(r'\x1b\[[0-9;]*m', '', narrow[0]).count(fc.PLACEHOLDER)), (1, 4))
        self.assertIs(self.images.place(self.path, 40, 20), lines)       # the same: not made again

    def test_sent_once_before_the_lines_that_show_it_then_cleared(self):
        lines = self.images.place(self.path, 40, 20)
        ident = next(iter(self.images.data))
        out = self.images.output(''.join(lines))
        self.assertIn(f'\x1b_Ga=T,U=1,f=100,t=d,i={ident},c=10,r=3,q=2,m=0;', out)
        payload = out.split(';', 1)[1].split('\x1b\\')[0]
        self.assertTrue(base64.b64decode(payload).startswith(b'\x89PNG'))
        self.assertEqual(self.images.output(''.join(lines)), '')        # the terminal has it
        self.images.forget()
        self.assertTrue(self.images.output(lines[0]))                  # a cleared screen: again
        self.assertEqual(self.images.clear(), f'\x1b_Ga=d,d=I,i={ident},q=2\x1b\\')

    def test_through_tmux_each_command_is_wrapped(self):
        lines = self.images.place(self.path, 40, 20)
        with patch.dict(os.environ, {'TMUX': '/tmp/tmux-1/default,1,0'}):
            out = self.images.output(lines[0])
        self.assertTrue(out.startswith('\x1bPtmux;\x1b\x1b_G') and out.endswith('\x1b\x1b\\\x1b\\'))

    def test_forgotten_pictures_leave_the_terminal(self):
        self.images.KEEP = 1
        first = self.images.place(self.path, 40, 20)
        self.images.output(first[0])
        old = next(iter(self.images.data))
        self.images.place(self.path, 30, 20)
        self.assertIn(f'a=d,d=I,i={old}', self.images.output(''))

    def test_the_pane_draws_kittys_pictures_and_noise_leaves_them_be(self):
        with patch.object(fc, 'GRAPHICS', 'kitty'), patch.object(fc, 'IMAGES', self.images):
            lines = fc.Picture(self.path).lines(40, 20)
        self.assertIn(fc.PLACEHOLDER, lines[0])
        self.assertEqual(fc.scramble(lines[0], 1.0), lines[0])
        self.assertNotIn(fc.PLACEHOLDER, fc.PLACED.sub(' ', lines[0]))
        with patch.object(fc, 'GRAPHICS', None):            # elsewhere: chafa's blocks, or a line saying what
            self.assertNotIn(fc.PLACEHOLDER, ''.join(fc.Picture(self.path).lines(40, 20)))


class Inline(unittest.TestCase):
    """A provider's picture() draws under a node's label on the canvas itself (a chat's photo)."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'p.png')
        with open(self.path, 'wb') as f:
            f.write(png(100, 50))
        path = self.path

        class Chat(fc.Provider):
            def root(self):
                return fc.Node('/', 'chat', True, source=self, kind='root')
            def children(self, node):
                return [fc.Node('/1', 'look at this', False, node, 'msg'),
                        fc.Node('/2', 'nice', False, node, 'msg')] if node.kind == 'root' else []
            def width(self, node):
                return 30
            def picture(self, node):
                return path if node.path == '/1' else None
        self.provider = Chat()
        for name, value in (('CELL', (10.0, 20.0)), ('IMAGES', fc.Images())):
            patcher = patch.object(fc, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def frame(self):
        c = fc.Canvas('', fc.options(provider=self.provider, backdrop='off', ease=1, glyphs='plain', start='/1'))
        return [re.sub(r'\x1b\[[0-9;]*m', '', l) for l in c.frame(100, 24, animate=False)[0]], c

    def test_the_picture_takes_rows_under_its_message_in_its_own_colour(self):
        with patch.object(fc, 'GRAPHICS', 'kitty'):
            lines, c = self.frame()
            raw = c.frame(100, 24, animate=False)[0]
        at = next(i for i, l in enumerate(lines) if 'look at this' in l)
        self.assertEqual([l.count(fc.PLACEHOLDER) for l in lines[at + 1:at + 4]], [10, 10, 10])
        self.assertIn('nice', lines[at + 4])
        self.assertTrue(fc.IMAGES.output('\n'.join(raw)))      # its id is in the colour: it's sent

    def test_elsewhere_no_rows(self):
        with patch.object(fc, 'GRAPHICS', None):
            lines, _ = self.frame()
        self.assertNotIn(fc.PLACEHOLDER, ''.join(lines))
        at = next(i for i, l in enumerate(lines) if 'look at this' in l)
        self.assertIn('nice', lines[at + 1])


if __name__ == '__main__':
    unittest.main()
