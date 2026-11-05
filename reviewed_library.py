"""Content-keyed reviewed-assembly restore is disabled in this clean workspace.

A fresh project must be built from its own uploaded SRT, production script and MP4s.
No case-specific reviewed timeline is bundled.
"""

def eligible(p, root, detail=False):
    return (False, 'No reviewed assembly library is installed in this clean workspace.') if detail else False


def queue_ready(p, root):
    return False


def prepare_case_reviewed(p, root, studio=None):
    return False


def restore_case_reviewed(p, root, studio=None):
    return False
