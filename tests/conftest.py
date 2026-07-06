import time

import pytest

from hive.state import Message


@pytest.fixture
def stranger_msg() -> Message:
    return Message(role="stranger", text="hello friend", ts=time.time(), msg_id=1)
