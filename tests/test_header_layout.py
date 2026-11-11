"""The header must put the name where the drawing puts it, and keep its buttons usable.

Two layouts have been wrong here in different ways. First the brand, the two library
buttons and the action group each took an equal share of the bar (`flex:1 1 0`), which
stretched two small groups of buttons across the whole window and left the production
name floating in the middle of a gap. Then the name was pulled left to sit beside the
controls, which put it nowhere in particular.

What the drawing asks for is exact: the two boxes stay over the media pool, and the
production name sits on the same vertical line as the play button in the transport
below -- the centre of the monitor column. That is a geometric promise, so the tests
here check the mechanism that delivers it (the header borrows the editor grid's own
column tracks, and the name is centred in the middle one) and, when a browser is
available, the pixels themselves.

They read the stylesheet because the property that matters is which rule wins the
cascade. They are deliberately about structure, not decoration: the bar can be
restyled freely, but the name must stay on the picture's centre line, the groups must
not be stretched apart, and no button may become unreachable.
"""
import re
import unittest
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / 'static' / 'editor.css').read_text(encoding='utf-8')
HTML = (Path(__file__).resolve().parent.parent / 'templates' / 'index.html').read_text(encoding='utf-8')


def _rules(selector: str) -> list[tuple[str, str]]:
    """Every (media-condition, declarations) for a selector, in source order.

    Handles the minified blocks this stylesheet mixes with formatted ones: a block ends
    at the first close brace either way.
    """
    found = []
    for match in re.finditer(r'([^{}]*?)\{([^{}]*)\}', CSS):
        selectors, body = match.group(1), match.group(2)
        # Skip the condition part of an @media opener.
        for part in selectors.split(','):
            if part.strip().split()[-1:] == [selector] or part.strip() == selector:
                # Walk back to see whether this block sits inside an @media rule.
                start = match.start()
                preceding = CSS[max(0, start - 400):start]
                media = re.findall(r'@media\(([^)]*)\)\{[^@]*$', preceding)
                found.append((media[-1] if media else '', body))
    return found


def _last(selector: str) -> str:
    rules = _rules(selector)
    return rules[-1][1] if rules else ''


def _base(selector: str) -> str:
    """The last rule for a selector that is NOT inside a media query.

    `_last` returns whatever comes last in the file, which for a layout rule is usually
    a narrow-screen override. The unconditional layout is the one to assert on.
    """
    base = [body for media, body in _rules(selector) if not media]
    return base[-1] if base else ''


def _decl(body: str, prop: str) -> str:
    match = re.search(rf'(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;}}]+)', body)
    return match.group(1).strip() if match else ''


class HeaderLayoutTest(unittest.TestCase):
    def test_the_header_contains_the_two_library_buttons(self):
        header = re.search(r'<header.*?</header>', HTML, re.S).group(0)
        self.assertIn('head-tools', header)
        self.assertIn('Transition', header)
        self.assertIn('Caption', header)

    def test_the_groups_are_not_given_equal_shares_of_the_bar(self):
        """This is the defect: an equal flex share stretched the controls apart and left
        the name floating in the gap between them."""
        for selector in ('.topbar>.brand', '.topbar>.head-tools', '.topbar>.top-actions'):
            body = _last(selector)
            flex = _decl(body, 'flex') or f'shown {_decl(body, "flex-grow")}'
            self.assertNotIn('1 1 0', flex.replace('  ', ' '),
                             f'{selector} takes an equal share again ({flex!r})')
            grow = _decl(body, 'flex-grow')
            if grow:
                self.assertEqual(grow, '0',
                                 f'{selector} grows and pushes its neighbours apart')

    def test_the_name_is_centred_in_the_monitors_own_column(self):
        """The drawing: the name sits on the vertical line of the play button.

        The mechanism is that the bar is a grid on the SAME tracks as the editor grid,
        and the name is the middle track's content, centred in it. A left-aligned name
        beside the boxes was the previous attempt and is what this replaces.
        """
        bar = _base('.topbar')
        self.assertIn('grid-template-columns:var(--pane-columns)', bar.replace(' ', ''),
                      'the header no longer shares the editor grid\'s column tracks')
        self.assertIn('--pane-columns:', CSS,
                      'the pane widths must be defined once, for both the header and the grid')
        name = _base('.topbar>.project-heading')
        self.assertEqual(_decl(name, 'justify-content'), 'center',
                         'a left-aligned name drifts wherever the controls end')
        # This one is subtle and cost a browser measurement to find: the shared
        # `.project-heading` rule (the one the documentary workspace uses) is a
        # COLUMN flexbox. In a column, `justify-content` acts on the vertical axis,
        # so `justify-content:center` centres nothing horizontally and the name is
        # centred only by `align-items`. That works by accident until someone
        # changes align-items -- and a test that asserts on justify-content alone
        # passes while the name sits hundreds of pixels off its line.
        self.assertEqual(_decl(name, 'flex-direction'), 'row',
                         'in a column flexbox, justify-content cannot centre the name '
                         'horizontally; the horizontal axis must be the main axis')
        self.assertNotIn('border-left', name,
                         'a rule between the controls and the name is not in the drawing')

    def test_the_grid_and_the_header_cannot_drift_apart(self):
        """One definition of the three pane widths, not two that can disagree."""
        self.assertEqual(CSS.count('--pane-columns:'), 5,
                         'the widths are redefined per breakpoint; each breakpoint must '
                         'move the header and the grid together')
        grid = _base('.editor-grid')
        self.assertIn('34fr', grid, 'the editor grid must keep its own proportional tracks')

    def test_the_actions_stay_on_the_right(self):
        # The rule that decides this is the one for the bar's own child, written last.
        body = _last('.topbar>.top-actions').replace(' ', '')
        self.assertIn('margin-left:auto', body,
                      'the action buttons no longer sit at the right end of the bar')

    def test_the_buttons_keep_a_readable_foreground(self):
        """The image showed the button text on no background at all. Whatever the cause,
        the rule that survives the cascade must keep a light colour and a dark fill."""
        body = _base('.head-tool')
        self.assertTrue(_decl(body, 'color'), 'the button has no text colour')
        self.assertTrue(_decl(body, 'background') or _decl(body, 'background-color'),
                        'the button has no fill')
        colour = _decl(body, 'color')
        self.assertIn(colour, ('#eef2f7', 'var(--text)', 'rgb(238, 242, 247)'),
                      f'the library buttons lost their readable colour ({colour!r})')

    def test_the_controls_are_never_hidden(self):
        """The drawing keeps the two boxes in the bar, so they stay there at every width.

        An earlier version hid them below 1240px "because the transport bar offers
        both". That was true and still wrong: the drawing puts them in the header, and
        a layout that deletes its own controls once the window narrows is not the
        layout that was asked for. They may shrink; they may not vanish.
        """
        for selector in ('.head-tools', '.head-tool'):
            for media, body in _rules(selector):
                compact = body.replace(' ', '')
                self.assertNotIn('display:none', compact,
                                 f'{selector} is hidden under @media({media})')
                self.assertNotIn('visibility:hidden', compact,
                                 f'{selector} is hidden under @media({media})')

    def test_no_action_button_is_hidden_at_any_width(self):
        """Each of these is the only route to its dialog, so hiding one strands it."""
        for selector in ('#setup-button', '#performance-button', '#documentary-button',
                         '#render-button'):
            for media, body in _rules(selector):
                self.assertNotIn('display:none', body.replace(' ', ''),
                                 f'{selector} is hidden under @media({media}), '
                                 f'and nothing else opens that dialog')


if __name__ == '__main__':
    unittest.main()
