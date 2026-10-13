"""Run durable stages on the current host: python -m agent_runtime.worker."""

import argparse
import asyncio
import signal
import time

import log
from local_app import create_local_services


async def run(services, *, once=False):
    logger = log.get_logger('parts_bin.worker')
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    reconciled = 0.0
    try:
        while not stopping.is_set():
            try:
                if time.monotonic() - reconciled >= 15 or once:
                    services.dispatch.reconcile()
                    reconciled = time.monotonic()
                worked = await services.dispatch.run_once()
            except Exception as exc:
                logger.error('Worker storage failure', extra={'error_type': type(exc).__name__})
                worked = False
            if once:
                return
            if not worked:
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=1)
                except TimeoutError:
                    pass
    finally:
        await services.close()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Reconcile and execute at most one due stage')
    parser.add_argument('--health', action='store_true', help='Check the durable worker heartbeat')
    args = parser.parse_args()
    services, _ = create_local_services()
    if args.health:
        raise SystemExit(0 if services.domain.repository.dispatch.healthy() else 1)
    asyncio.run(run(services, once=args.once))


if __name__ == '__main__':
    main()
