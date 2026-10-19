import asyncio
import threading
from types import SimpleNamespace

import pytest

from fiber_bundle import database, intake_abort_commands
from fiber_bundle.organization import commands


@pytest.mark.parametrize("deliver", [intake_abort_commands.deliver, commands.deliver])
def test_preclaim_storage_wait_does_not_stop_receiver_or_send(config, scope, deliver):
    acquired, release = threading.Event(), threading.Event()

    def writer():
        with database.locked(config):
            acquired.set()
            release.wait(4)

    thread = threading.Thread(target=writer)
    thread.start()
    assert acquired.wait(2)
    sends, pulses = [], []

    async def sender(*args):
        sends.append(args)
        return True

    async def run():
        async def heartbeat():
            while True:
                pulses.append(True)
                await asyncio.sleep(0.03)

        pulse = asyncio.create_task(heartbeat())
        try:
            await deliver(config, SimpleNamespace(validate=lambda: None), sender, lambda: None)
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)

    try:
        asyncio.run(run())
    finally:
        release.set()
        thread.join(3)
    assert len(pulses) >= 3 and sends == []
