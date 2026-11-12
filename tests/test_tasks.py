"""The bundled task desk (pytest, with no standalone TuiDo installation): its tree, marking, planning, adding, continuing, undo, and watching the
file for changes made beside it. Each test has its own database; none touches yours."""
import re
import sqlite3
import time
from datetime import date

import unittest

try:
    import pytest
except ImportError:         # these are pytest tests; unittest can run the other modules
    raise unittest.SkipTest('tasks: install twigs-cli[test] and run pytest')

from twigs import core as tc
from twigs.apps.tasks.canvas import Desk, boxes
from twigs.apps.tasks.model.store import Store

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


@pytest.fixture
def desk(tmp_path):
    store = Store(tmp_path / "desk.sqlite3")
    store.seed_demo()
    yield store
    store.close()


def canvas(store, path=None, watch=0):
    return tc.Canvas("", tc.options(provider=Desk(store, path, watch), backdrop="off", ease=1, glyphs="plain",
                                    colmax=40, peek=True))


def press(c, *keys):
    result = None
    for k in keys:
        result = tc.apply(c, k, c.opts)
    return result


def screen(c, w=140, h=30):
    return [ANSI.sub("", l).rstrip() for l in c.frame(w, h, animate=False)[0]]


def find(c, *names):
    """Go to the node down this way of names (labels' beginnings)."""
    node = c.root
    ids = []
    for name in names:
        node = next(k for k in node.load() if k.label().startswith(name))
        ids.append(node.path)
    assert c.go(ids)
    return node


def test_the_desk_branches_into_its_sections_and_down(desk):
    c = canvas(desk)
    assert [k.name for k in c.root.load()] == ["Today", "Earlier pages", "Paths", "Library", "Habits", "Skills",
                                              "Projects", "Ideas"]
    book = find(c, "Library", "Linear Algebra")
    assert book.label() == "Linear Algebra\n██ ██ ██ ▓▓ ░░ ░░ ░░ ░░  3/8"     # under it: three read, one under way
    chapters = [k for k in book.load() if k.kind == "chapter"]
    assert [k.name for k in chapters][:2] == ["Systems of equations", "Row reduction"]
    assert book.source.mark(chapters[0])[2] == "x" and book.source.mark_before(chapters[0])
    assert book.source.colour(chapters[0]) == ("state_done", False)
    assert book.source.line(chapters[0]) == "state_done"
    assert book.source.mark(chapters[3])[2] == "~" and book.source.colour(chapters[3])[0] == "state_doing"
    thesis = find(c, "Projects", "Thesis chapter 2")
    assert thesis.source.mark(thesis)[1] == "★"                 # pinned
    outline = next(k for k in thesis.load() if k.name == "Outline")
    assert outline.is_dir and [k.name for k in outline.load() if k.kind == "task"][0] == "List the three main arguments"
    laundry = next(k for k in find(c, "Projects", "Home").load() if k.name == "Laundry")
    assert laundry.label() == "Laundry · every 7 days · 9 days ago"
    habit = find(c, "Habits", "Read")
    assert re.fullmatch(r"Read\n[■□·]( [■□·])*", habit.label())                # this week, under it
    notes = [k for k in habit.load() if k.kind == "info"]
    assert notes and "M " in notes[0].label()                    # on the canvas: the heatmap, a row a weekday
    assert "Earlier pages" in "".join(screen(c))


def test_enter_marks_things_and_opens_branches(desk):
    c = canvas(desk)
    today = find(c, "Today")
    task = next(k for k in today.load() if k.name == "Email supervisor")
    find(c, "Today", "Email supervisor")
    press(c, "\r")
    assert desk.rows("SELECT done FROM bullets WHERE text='Email supervisor'")[0]["done"] == 1
    screen(c)
    assert c.focus.source.mark(c.focus)[1] == "✓"
    chapter = find(c, "Library", "Linear Algebra", "Linear independence")
    press(c, "\r")
    assert desk.rows("SELECT reading FROM chapters WHERE title='Linear independence'")[0]["reading"] == 2
    find(c, "Habits", "Exercise")
    before = desk.habit_counts(desk.rows("SELECT id FROM habits WHERE title='Exercise'")[0]["id"]).get(date.today().isoformat(), 0)
    press(c, "\r")
    after = desk.habit_counts(desk.rows("SELECT id FROM habits WHERE title='Exercise'")[0]["id"]).get(date.today().isoformat(), 0)
    assert after == before + 1
    outline = find(c, "Projects", "Thesis chapter 2", "Outline")
    press(c, "\r")                                               # a branch: it opens, nothing marked
    assert c.focus.parent is outline
    assert desk.rows("SELECT status FROM project_tasks WHERE title='Outline'")[0]["status"] == 0


def test_plan_puts_it_in_today_linked_and_typed_lines_never_close_the_canvas(desk):
    c = canvas(desk)
    find(c, "Library", "Linear Algebra", "Eigenvalues")
    press(c, "\x10")
    assert c.asking[1] == "Read: Eigenvalues"                    # its title, to change if you like
    assert press(c, "\r") is None
    planned = desk.rows("SELECT * FROM bullets WHERE text='Read: Eigenvalues'")[0]
    assert planned["day"] == date.today().isoformat() and planned["chapter_id"]
    find(c, "Today")
    press(c, "\x0e")
    assert press(c, "\r") is None                                # nothing typed: nothing added, still open
    press(c, "\x0e", *"- remember the library", "\r")
    note = desk.rows("SELECT * FROM bullets WHERE text='remember the library'")[0]
    assert note["note"] == 1


def test_add_rename_delete_and_undo(desk):
    c = canvas(desk)
    find(c, "Projects", "Thesis chapter 2", "Send to supervisor")
    press(c, "\x0e", *"Book a meeting", "\r")
    step = desk.rows("SELECT * FROM project_tasks WHERE title='Book a meeting'")[0]
    assert step["parent_id"] is None                             # beside the step, in the project
    screen(c)                                                    # a frame takes the change in
    find(c, "Projects", "Thesis chapter 2", "Book a meeting")
    press(c, "\x05", "\x15", *"Book a meeting with Dr Lee", "\r")
    assert desk.rows("SELECT title FROM project_tasks WHERE id=?", (step["id"],))[0]["title"] == "Book a meeting with Dr Lee"
    press(c, "\x04", "y")
    assert not desk.rows("SELECT * FROM project_tasks WHERE id=?", (step["id"],))
    assert c.statuses["act"][1] == "gone"                        # it goes to dust
    press(c, "\x1a")
    assert desk.rows("SELECT title FROM project_tasks WHERE id=?", (step["id"],))
    screen(c)
    assert any(k.name == "Book a meeting with Dr Lee" for k in find(c, "Projects", "Thesis chapter 2").load())


def test_continue_flies_to_the_next_activity_ready(desk):
    c = canvas(desk)
    path = find(c, "Paths", "Learning to learn")
    press(c, "\x14")
    nxt = desk.next_activity(path.data["id"])
    assert c.focus.kind == "activity" and c.focus.data["id"] == nxt
    screen(c)
    assert c.focus.path in c.pulses                              # and it flashes as you land


def test_an_earlier_task_comes_forward_to_today(desk):
    c = canvas(desk)
    old = find(c, "Earlier pages", "yesterday", "Pick up library holds")
    press(c, "\x14")
    assert desk.rows("SELECT migrated FROM bullets WHERE id=?", (old.data["id"],))[0]["migrated"] == 1
    assert desk.rows("SELECT * FROM bullets WHERE text='Pick up library holds' AND day=?", (date.today().isoformat(),))


def test_focus_narrows_it_as_it_does_the_desk(desk):
    desk.set_view(focus=True)
    c = canvas(desk)
    assert [k.name for k in find(c, "Projects").load()] == ["Thesis chapter 2"]


def test_changes_made_beside_it_show(tmp_path):
    path = tmp_path / "desk.sqlite3"
    store = Store(path)
    store.add_project("Kiln shed")
    c = canvas(store, path, watch=0.05)
    projects = find(c, "Projects")
    other = sqlite3.connect(path)
    other.execute("INSERT INTO projects(title) VALUES ('Garden')")
    other.commit()
    other.close()
    for _ in range(60):
        time.sleep(0.05)
        if c.source.changed:
            break
    screen(c)
    assert [k.name for k in projects.load()] == ["Kiln shed", "Garden"]
    store.close()


def test_bars_are_blocks_a_part_or_one_bar_in_proportion():
    assert boxes([2, 1, 0]) == "████ ▓▓▓▓ ░░░░  1/3"           # a block a part, room for four each
    assert boxes([2] * 6 + [0] * 6) == "█ █ █ █ █ █ ░ ░ ░ ░ ░ ░  6/12"
    long = boxes([2] * 10 + [1] * 5 + [0] * 25)                  # too many for blocks: one bar
    assert long == "██████" + "▓▓▓" + "░" * 15 + "  10/40"
    assert boxes([]) == ""


def test_notes_sit_on_the_canvas_before_the_parts_and_enter_still_marks(desk):
    c = canvas(desk)
    path = find(c, "Paths", "Learning to learn")
    kids = path.load()
    notes = [k.label() for k in kids if k.kind == "info"]
    assert kids[0].kind == "info" and notes[0].startswith("Build a sustainable")  # what it's for, first
    assert notes[-1].startswith("next: ")                        # then what's next
    press(c, "right")
    assert c.focus.kind == "activity"                            # going in lands past the notes
    leaf = find(c, "Paths", "Learning to learn", "Find your question", "Read: asking useful questions")
    assert leaf.is_dir and leaf.load()[0].label().startswith("source: ")         # its source, beside it
    press(c, "\r")
    assert c.focus is leaf                                       # marked, not opened
    assert desk.rows("SELECT status FROM activities WHERE id=?", (leaf.data["id"],))[0]["status"] == 1
    assert not c.source.pane                                     # no pane: it's all on the canvas


def keys(c, data):
    """Keys as a terminal sends them (Alt+arrows too), through the canvas's own reading of them."""
    for k in tc.keys(data):
        tc.apply(c, k, c.opts)


def test_add_inside_a_step_an_activity_and_a_habit(desk):
    c = canvas(desk)
    find(c, "Projects", "Thesis chapter 2", "Send to supervisor")
    press(c, "\x01", *"Attach the outline", "\r")
    send = desk.rows("SELECT id FROM project_tasks WHERE title='Send to supervisor'")[0]["id"]
    assert desk.rows("SELECT parent_id FROM project_tasks WHERE title='Attach the outline'")[0]["parent_id"] == send
    find(c, "Paths", "Learning to learn", "Find your question", "Read: asking useful questions")
    press(c, "\x01", *"Underline three questions", "\r")
    reading = desk.rows("SELECT id FROM activities WHERE title='Read: asking useful questions'")[0]["id"]
    assert desk.rows("SELECT parent_id FROM activities WHERE title='Underline three questions'")[0]["parent_id"] == reading
    find(c, "Habits", "Exercise")
    press(c, "\x01", *"Swimming", "\r")
    swim = desk.rows("SELECT * FROM habits WHERE title='Swimming'")[0]
    exercise = desk.rows("SELECT * FROM habits WHERE title='Exercise'")[0]
    assert swim["parent_id"] == exercise["id"] and swim["unit"] == exercise["unit"] == "minutes"
    screen(c)
    assert any(k.name == "Swimming" for k in find(c, "Habits", "Exercise").load())     # a sub-habit, under it
    press(c, "\x0e", *"Cycling", "\r")
    assert desk.rows("SELECT parent_id FROM habits WHERE title='Cycling'")[0]["parent_id"] is None   # beside it


def test_alt_arrows_nest_lift_and_move_a_step_and_you_go_with_it(desk):
    c = canvas(desk)
    find(c, "Projects", "Thesis chapter 2", "Write the messy first draft")
    keys(c, "\x1b[1;3C")                                         # ⌥→: under the step above (Outline)
    outline = desk.rows("SELECT id FROM project_tasks WHERE title='Outline'")[0]["id"]
    draft = desk.rows("SELECT * FROM project_tasks WHERE title='Write the messy first draft'")[0]
    assert draft["parent_id"] == outline
    assert c.focus.name == "Write the messy first draft" and c.focus.parent.name == "Outline"
    keys(c, "\x1b[1;3A")                                         # ⌥↑: before the step above it
    order = [r["title"] for r in desk.rows("SELECT title FROM project_tasks WHERE parent_id=? ORDER BY position", (outline,))]
    assert order.index("Write the messy first draft") == len(order) - 2
    keys(c, "\x1b[1;3D")                                         # ⌥←: back out, just below Outline
    assert desk.rows("SELECT parent_id FROM project_tasks WHERE id=?", (draft["id"],))[0]["parent_id"] is None
    assert c.focus.name == "Write the messy first draft" and c.focus.parent.name == "Thesis chapter 2"
    keys(c, "\x1b[1;3D")
    assert c.statuses["act"][:2] == ("it is at the top already", "note")
    press(c, "\x1a", "\x1a", "\x1a")                             # and undo, a step at a time
    assert desk.rows("SELECT parent_id FROM project_tasks WHERE id=?", (draft["id"],))[0]["parent_id"] is None


def test_w_says_when_a_task_is_due_and_it_calls_once_its_late(desk):
    c = canvas(desk)
    p = c.source
    p.today = lambda: date(2026, 10, 30)                          # a Friday
    thesis = find(c, "Projects", "Thesis chapter 2")
    step = next(k for k in thesis.load() if k.kind == "task" and not k.is_dir and p.state(k) != 2)
    assert c.go([thesis.parent.path, thesis.path, step.path])
    press(c, "\x17", *"mon", "\r")                                # ⌃W: due Monday
    screen(c)
    step = c.focus
    assert step.data["due"] == "2026-11-02"
    assert p.count(step) == ("due Mon", "stub", False)
    assert p.beacon(step) is None and p.beacon(thesis) is None
    p.today = lambda: date(2026, 11, 4)                           # two days on: late, calling
    p.invalidate()
    screen(c)
    assert p.count(c.focus) == ("2 days late", "git_conflict", True)
    late_since = time.mktime(date(2026, 11, 3).timetuple())
    assert p.beacon(c.focus) == late_since
    assert p.beacon(c.focus.parent) == late_since                 # its project too
    press(c, "\x17", "\r")                                        # said again as it was: still late, not next year's
    screen(c)
    assert c.focus.data["due"] == "2026-11-02"
    press(c, "\x17", *["\x7f"] * 8, "\r")                         # nothing: not due any more
    screen(c)
    assert c.focus.data["due"] is None and p.beacon(c.focus) is None
    press(c, "\x1a")                                              # one undo step: due again
    screen(c)
    assert c.focus.data["due"] == "2026-11-02"


def test_s_starts_a_timer_that_counts_down_then_rings_until_seen(desk, monkeypatch):
    from twigs.kit import notify
    told = []
    monkeypatch.setattr(notify, "notify", lambda title, body: told.append((title, body)))
    c = canvas(desk)
    p = c.source
    now = [1_000_000.0]
    p.clock = lambda: now[0]
    thesis = find(c, "Projects", "Thesis chapter 2")
    step = next(k for k in thesis.load() if k.kind == "task")
    assert c.go([thesis.parent.path, thesis.path, step.path])
    press(c, "\x13", *"1h30", "\r")
    assert p.count(c.focus)[:2] == ("⏱ 90m", "state_doing")
    now[0] += 60 * 30 + 10
    assert p.count(c.focus)[0] == "⏱ 60m"
    assert p.next_change() == now[0] + 50                          # the next minute down
    end = p.timers[c.focus.path][0]
    p.ring(c.focus.path, c.focus.name, end)                         # its time comes
    assert told == [("Time's up", step.name)]
    assert p.beacon(c.focus) == end and c.focus.path not in p.timers
    p.focus(c.focus)                                               # looked at: it stops
    assert p.beacon(c.focus) is None
    press(c, "\x13", "\r")                                         # 25 by default; ⌃S again stops it
    assert p.count(c.focus)[0] == "⏱ 25m"
    press(c, "\x13")
    assert c.focus.path not in p.timers


def test_when_reads_days_and_lengths():
    from twigs.kit import when
    fri = date(2026, 10, 30)
    assert [when.day(t, fri) for t in ("fri", "tomorrow", "mon", "3 nov", "nov 3", "+3d", "2w", "next week")] == \
        [fri, date(2026, 10, 31), date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 3), date(2026, 11, 2),
         date(2026, 11, 13), date(2026, 11, 2)]
    assert when.day("1 jan", fri) == date(2027, 1, 1) and when.day("soon", fri) is None and when.day("31 feb", fri) is None
    assert [when.minutes(t) for t in ("25", "25m", "1h", "1h30", "90 min", "", "x")] == [25, 25, 60, 90, 90, None, None]


def test_today_entries_move_persist_and_undo(desk):
    c = canvas(desk)
    selected = find(c, 'Today', 'Email supervisor')
    before = [n.data['id'] for n in selected.parent.load() if n.kind == 'bullet']
    press(c, 'alt-up')
    screen(c)
    after = [n.data['id'] for n in c.focus.parent.load() if n.kind == 'bullet']
    assert after == [before[1], before[0], *before[2:]]
    assert c.focus.path == selected.path
    reopened = canvas(desk)
    assert [n.data['id'] for n in find(reopened, 'Today').load() if n.kind == 'bullet'] == after
    press(c, '\x1a')
    screen(c)
    assert [n.data['id'] for n in c.focus.parent.load() if n.kind == 'bullet'] == before
    find(c, 'Today', 'Email supervisor')
    press(c, 'alt-down')
    screen(c)
    assert [n.data['id'] for n in c.focus.parent.load() if n.kind == 'bullet'] == [before[0], before[2], before[1]]
    press(c, 'alt-down')
    screen(c)
    assert c.focus.data['id'] == before[1]


def test_today_move_from_combined_twigs_keeps_focus(desk):
    from twigs.kit.together import Together
    provider = Desk(desk, watch=0)
    both = Together([('TuiDo', provider)])
    c = tc.Canvas('', tc.options(provider=both, backdrop='off', ease=1))
    selected = find(c, 'TuiDo', 'Today', 'Email supervisor')
    press(c, 'alt-up')
    screen(c)
    assert c.focus.path == selected.path
    assert next(n for n in c.focus.parent.load() if n.kind == 'bullet').path == selected.path


def test_a_step_added_from_combined_twigs_shows_pinned_or_not(desk):
    from twigs.kit.together import Together
    c = tc.Canvas('', tc.options(provider=Together([('TuiDo', Desk(desk, watch=0))]), backdrop='off', ease=1))
    find(c, 'TuiDo', 'Today')
    press(c, 'f1')
    project = find(c, 'TuiDo', 'Projects', 'Thesis')
    press(c, 'f2')                                  # the project pinned: the pinboard
    screen(c)
    find(c, 'TuiDo', 'Projects', 'Thesis', 'Send to supervisor')
    press(c, '\x0e', *'Book the room', '\r')        # the desk's own invalidate() must still run
    assert any('Book the room' in l for l in screen(c, 160, 45))
    assert c.focus.name == 'Book the room'          # and you're on it: the next goes beside it
    press(c, '\x01', *'Ask the porter', '\r')       # a subtask: its step opens for it, pinned or not
    assert c.focus.name == 'Ask the porter' and c.focus.parent.name == 'Book the room'
    assert any('Ask the porter' in l for l in screen(c, 160, 45))
    press(c, 'alt-left')                            # lifting it out follows it here too
    assert c.focus.name == 'Ask the porter' and c.focus.parent is project
    press(c, 'f10')
    assert any('Book the room' in l for l in screen(c, 160, 45))


def combined(desk, *more):
    from twigs.kit.together import Together
    provider = Desk(desk, watch=0)
    provider.meet([provider, *[p for _, p in more]])
    return tc.Canvas('', tc.options(provider=Together([('TuiDo', provider), *more]), backdrop='off', ease=1)), provider


def test_continue_flies_there_from_combined_twigs_too(desk):
    c, _ = combined(desk)
    find(c, 'TuiDo', 'Paths', 'Learning to learn')
    press(c, '\x14')
    assert c.focus.kind == 'activity' and c.focus.path.startswith('/paths/p')


def test_what_the_model_can_do_the_canvas_can(desk):
    c = canvas(desk)
    skill = find(c, 'Skills').load()[0]
    c.go(c.chain(skill))
    press(c, '\r')                                              # Enter: a skill's level, a step on
    assert desk.rows('SELECT level FROM skills WHERE id=?', (skill.data['id'],))[0]['level'] == 1
    screen(c)
    find(c, 'Projects', 'Thesis chapter 2', 'Send to supervisor')
    press(c, '\x0f', *'by Friday', '\r')                         # ⌃O: its note
    press(c, '\x19', *'week', '\r')                              # ⌃Y: it repeats, and the note stays
    row = desk.rows("SELECT notes, every_days FROM project_tasks WHERE title='Send to supervisor'")[0]
    assert (row['notes'], row['every_days']) == ('by Friday', 7)
    screen(c)
    find(c, 'Projects', 'Thesis chapter 2', 'Send to supervisor')
    press(c, '\x19', '\x15', '\r')                               # nothing: it stops
    assert desk.rows("SELECT every_days FROM project_tasks WHERE title='Send to supervisor'")[0]['every_days'] is None
    screen(c)
    home = find(c, 'Projects', 'Home')
    assert not home.data['pinned']
    press(c, '\x06')                                            # ⌃F: starred
    assert desk.rows("SELECT pinned FROM projects WHERE title='Home'")[0]['pinned'] == 1
    press(c, '\x06')
    screen(c)
    projects = find(c, 'Projects')
    press(c, '\x06')                                            # on the list: only the starred
    screen(c)
    assert [k.name for k in projects.load() if k.kind == 'project'] == ['Thesis chapter 2']
    assert projects.label() == 'Projects · ★ only'
    press(c, '\x06')
    press(c, '\x0b', *'home', '\r')                              # ⌃K on the list: one shelf
    screen(c)
    assert [k.name for k in projects.load() if k.kind == 'project'] == ['Home'] and projects.label() == 'Projects · Home'
    press(c, '\x0b', '\x15', '\r')
    screen(c)
    find(c, 'Projects', 'Thesis chapter 2')
    press(c, '\x0b', *'Writing', '\r')                           # ⌃K on one: a shelf, made if it's new
    assert desk.rows("SELECT s.title FROM projects p JOIN shelves s ON s.id=p.shelf_id WHERE p.title='Thesis chapter 2'") \
        == [{'title': 'Writing'}]
    screen(c)
    find(c, 'Ideas', 'Learn to bake sourdough')
    press(c, '\x10')                                            # ⌃P: taken up as a project, and you go to it
    assert c.focus.kind == 'project' and c.focus.name == 'Learn to bake sourdough'
    press(c, '\x1a')
    assert not desk.rows("SELECT id FROM projects WHERE title='Learn to bake sourdough'")


def test_today_begins_with_what_the_calendar_has_on(desk):
    import datetime as dt
    from twigs.apps.cal import cal
    zone = dt.timezone.utc
    noon = dt.datetime.combine(date.today(), dt.time(12), zone)
    events = [cal.Event('trip', 'Lisbon', date.today(), date.today() + cal.DAY * 3),
              cal.Event('early', 'Standup', noon.replace(hour=9), noon.replace(hour=10)),
              cal.Event('late', 'Dentist', noon.replace(hour=15), noon.replace(hour=16))]

    class Diary(tc.Tree):
        went = None
        def on(self, day):
            return events if day == date.today() else []
        def way(self, day):
            self.went = day
            return self.ids
        def soon(self, e):
            return 'in 3h' if e.uid == 'late' else None
        def now(self):
            return noon

    diary = Diary({'Nov': {}})
    c, provider = combined(desk, ('Calendar', diary))
    diary.ids = list(c.chain(find(c, 'Calendar', 'Nov')))
    today = find(c, 'TuiDo', 'Today')
    names = [k.name for k in today.load()]
    assert names[1:4] == ['Lisbon', '09:00 Standup', '15:00 Dentist'] and names[4] == 'Read: Linear independence'
    assert provider.start_at(today) == 4                        # going in lands on the first thing to do
    standup, dentist = today.load()[2:4]
    assert provider.dim(standup) < 1 and provider.dim(dentist) == 1      # what's over is quieter
    assert provider.count(dentist)[0] == 'in 3h' and provider.count(standup) is None
    c.go(c.chain(dentist))
    press(c, '\x0f')                                            # ⌃O: to that day in the calendar
    assert diary.went == date.today() and c.focus.name == 'Nov'
    assert all(k.kind != 'event' for k in find(canvas(desk), 'Today').load())      # alone: as it was


def test_jump_finds_a_step_in_a_project_never_opened(desk):
    c, _ = combined(desk)
    press(c, '\x00', *'source argument')
    assert c.jumping['hits'][0][0].name == 'Find one source per argument'
    assert 'Thesis chapter 2' in '\n'.join(screen(c))            # and says where it is
    press(c, '\r')
    assert c.jumping is None and c.focus.name == 'Find one source per argument'

