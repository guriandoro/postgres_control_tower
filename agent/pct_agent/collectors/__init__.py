"""Periodic collectors that push state to the manager.

Each collector is a coroutine of shape ``async def loop(settings, state) -> None``
that runs forever; the agent's lifespan in :mod:`pct_agent.main` schedules
them with ``asyncio.create_task``.
"""
