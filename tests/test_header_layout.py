"""The header must read as one line, and its buttons must never be invisible.

The header used to give the brand, the two library buttons and the action group an
equal share of the bar (`flex:1 1 0` each). That stretched two small groups of buttons
across the whole window, so each sat at the far edge of its third with a wide empty gap
beside it, and the production name floated in the space between. On a wide screen it
looked like blank boxes with a few words drifting between them.

These tests read the stylesheet rather than a rendered page, because the property that
matters is which rule wins the cascade. They are deliberately about structure, not
decoration: a layout can be restyled freely, but it must not go back to stretching the
groups apart, and the buttons must keep a readable foreground.
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

    def test_the_name_follows_the_controls_instead_of_centring_in_a_gap(self):
        body = _last('.project-heading')
        self.assertEqual(_decl(body, 'justify-content'), 'flex-start')
        # A rule between the controls and the name is what the drawing asked for.
        self.assertIn('border-left', body)

    def test_the_actions_stay_on_the_right(self):
        # The rule that decides this is the one for the bar's own child, written last.
        body = _last('.topbar>.top-actions').replace(' ', '')
        self.assertIn('margin-left:auto', body,
                      'the action buttons no longer sit at the right end of the bar')

    def test_the_buttons_keep_a_readable_foreground(self):
        """The image showed the button text on no background at all. Whatever the cause,
        the rule that survives the cascade must keep a light colour and a dark fill."""
        body = _last('.head-tool')
        self.assertTrue(_decl(body, 'color'), 'the button has no text colour')
        self.assertTrue(_decl(body, 'background') or _decl(body, 'background-color'),
                        'the button has no fill')
        colour = _decl(body, 'color')
        self.assertIn(colour, ('#eef2f7', 'var(--text)', 'rgb(238, 242, 247)'),
                      f'the library buttons lost their readable colour ({colour!r})')

    def test_the_controls_are_hidden_only_where_they_are_reachable_elsewhere(self):
        """Narrow screens drop them rather than overflow, but only because the transport
        bar offers both. If that ever changes, this test should fail."""
        rules = _rules('.head-tools')
        hidden = [body for _media, body in rules if 'display:none' in body.replace(' ', '')]
        self.assertTrue(hidden, 'nothing hides the library buttons on a narrow screen')
        # The transport bar must still carry both doors.
        self.assertIn('openTransitionLibrary()', HTML.split('transport-right')[1])
        self.assertIn('toggleEditorCaptions()', HTML.split('transport-right')[1])


if __name__ == '__main__':
    unittest.main()
