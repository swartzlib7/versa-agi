"""Test package.

The model catalog a test reads is the repository's, not this machine's
installed one: `model_catalog` prefers /etc/versa-agi/models.ini, and an
install whose catalog differs from the source made three tests fail on that
machine only. An explicit VERSA_MODELS_INI still wins.
"""

import os

_REPO_MODELS_INI = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 "..", "..", "..", "models.ini"))
if os.path.isfile(_REPO_MODELS_INI):
    os.environ.setdefault("VERSA_MODELS_INI", _REPO_MODELS_INI)
