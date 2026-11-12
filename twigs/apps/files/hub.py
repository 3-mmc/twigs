"""Files beside mail, chats and the rest: your home folder as a section of `twigs`, its pictures,
PDFs and videos previewed in the pane; Enter or ⌃O opens a file in its app."""
import os

from twigs import core as fc


class Files(fc.FileSystem):
    pane = True

    def __init__(self):
        super().__init__(git=True, ls=bool(os.environ.get('LS_COLORS')), recent=True)
        self.home = os.path.expanduser('~')

    def loading(self):
        """(what's loading, whether it's done), for the opening screen."""
        return 'your files', True

    def root(self):
        return fc.Node(self.home, '~', True, kind='di', source=self)

    def children(self, node):
        out = super().children(node)
        if node.path == self.home and (node.parent is None or node.parent.source is not self):
            out.append(fc.Node('/', '/', True, node, 'di'))      # the rest of the disk, last
        return out

    def label(self, node):
        if node.parent is not None and node.parent.source is not self:
            return node.name                    # the section itself: Files, as the others are named
        return '/ (everything)' if node.path == '/' and node.parent.path == self.home else super().label(node)

    def locate(self, start):
        return ([], '') if not start else super().locate(start)

    def choose(self, node):
        if node.is_dir:
            return None                 # opens, on the canvas
        self.open(node)
        return fc.STAY

    def actions(self, node):
        return [('\x0f', 'open', '⌃O open')] if not node.is_dir else [('\x0f', 'open', '⌃O open in the file manager')]

    def act(self, node, name, canvas):
        if name == 'open':
            self.open(node)
            canvas.say(f'opening {node.name}')
        return None

    @staticmethod
    def open(node):
        from twigs.apps.msg import files
        files.open_file(node.path)


def sections():
    return [('Files', Files())]
