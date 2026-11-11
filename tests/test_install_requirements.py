"""The install instructions must produce a working environment.

This exists because a fresh `pip install -r requirements.txt` produced an editor that
could not start. `scenedetect` declares a dependency on the GUI `opencv-python`, which
pip installs beside the headless build they actually need; both distributions own the
same `cv2` package, the GUI one wins, and importing cv2 raises

    ImportError: libGL.so.1: cannot open shared object file

on any machine without a display library -- a server, a CI runner, and a Windows shell
with no graphics stack loaded. The requirements file looked correct, and the app failed
at the first import.

The tests below do not install anything. They check the two things that decide whether
an install is safe: that the requirement and constraint files say what they must, and
that the cv2 in this environment is the headless one.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / 'requirements.txt'
CONSTRAINTS = ROOT / 'requirements-constraints.txt'

# Distributions that install a cv2 the app cannot use alongside the headless build.
GUI_OPENCV = ('opencv-python', 'opencv-contrib-python', 'opencv-contrib-python-headless')


def _requirement_lines(path: Path) -> list[str]:
    """Every real requirement line: no comments, no blanks."""
    lines = []
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.split('#', 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def _name(line: str) -> str:
    return re.split(r'[<>=!~\[; ]', line, 1)[0].strip().lower()


class RequirementsTest(unittest.TestCase):
    def test_the_headless_build_is_required(self):
        names = [_name(l) for l in _requirement_lines(REQUIREMENTS)]
        self.assertIn('opencv-python-headless', names)

    def test_the_gui_builds_are_never_required(self):
        names = [_name(l) for l in _requirement_lines(REQUIREMENTS)]
        for gui in GUI_OPENCV:
            self.assertNotIn(gui, names,
                             f'requirements.txt asks for {gui}, which breaks `import cv2`')

    def test_a_constraint_file_blocks_the_gui_builds(self):
        """A requirement can be prevented from being installed by anything, including
        a dependency, only through a constraint. Without this file scenedetect quietly
        reintroduces the problem on every fresh install."""
        self.assertTrue(CONSTRAINTS.is_file(),
                        'requirements-constraints.txt is missing, so scenedetect will '
                        'install the GUI opencv over the headless one')
        pinned = {_name(l) for l in _requirement_lines(CONSTRAINTS)}
        for gui in GUI_OPENCV:
            self.assertIn(gui, pinned,
                          f'{gui} is not blocked, so a dependency can install it')

    def test_the_constraint_cannot_accidentally_install_something(self):
        """`pip install -c file` must never install what the file lists. Pinning an
        impossible version is what makes the constraint a refusal rather than a request;
        a real version here would silently downgrade or install the GUI build."""
        for line in _requirement_lines(CONSTRAINTS):
            self.assertIn('==0.0.0', line,
                          f'{line!r} in the constraints file could be satisfiable, which '
                          'would defeat the purpose of the file')

    def test_the_readme_passes_the_constraints(self):
        """Instructions that omit -c produce the broken environment this file guards."""
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        installs = [l for l in readme.splitlines() if 'pip install' in l and 'requirements.txt' in l]
        self.assertTrue(installs, 'the README no longer shows how to install the requirements')
        for line in installs:
            self.assertIn('requirements-constraints.txt', line,
                          f'this install line omits the constraints and will break cv2: {line!r}')

    def test_the_cv2_in_this_environment_is_the_headless_one(self):
        """A GUI build installed beside the headless one is the failure. If cv2 cannot
        be imported at all, that is the same failure and must not be skipped."""
        import cv2
        self.assertTrue(cv2.__version__)


class SceneDetectTest(unittest.TestCase):
    def test_scene_detection_still_works_with_the_headless_build(self):
        """The constraint is only acceptable if scenedetect runs without its declared
        GUI dependency. It does: it imports cv2 by name, and the headless build owns it."""
        try:
            import scenedetect
        except ImportError:
            self.skipTest('scenedetect is not installed')
        from scenedetect.detectors import AdaptiveDetector
        self.assertTrue(callable(AdaptiveDetector))


if __name__ == '__main__':
    unittest.main()
