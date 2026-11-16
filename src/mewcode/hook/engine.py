"""Hook engine: rule dispatch, blocking decisions, and only_once state."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field

from mewcode.hook.event import Event, is_blocking
from mewcode.hook.executor import Executor
from mewcode.hook.matcher import eval_condition
from mewcode.hook.rule import Payload, Rule


@dataclass
class DispatchResult:
    blocked: bool = False
    reason: str = ""
    blocking_hook_name: str = ""
    injected_prompts: list[str] = field(default_factory=list)


class Engine:
    def __init__(self, rules: list[Rule], sources: list[str]) -> None:
        self._rules = list(rules)
        self._sources = list(sources)
        self._once_fired: set[str] = set()
        self._lock = asyncio.Lock()
        self._executor = Executor()
        self._tasks: set[asyncio.Task] = set()

    async def dispatch(self, event: Event, payload: Payload) -> DispatchResult:
        result = DispatchResult()
        for rule in self._rules:
            if rule.event is not event:
                continue
            if not eval_condition(rule.condition, payload):
                continue
            if rule.only_once and not await self._claim_once(rule):
                continue

            if rule.asyncio_mode:
                task = asyncio.create_task(self._run_async(rule, payload))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
                continue

            try:
                outcome = await self._executor.run(rule, payload, blocking=is_blocking(event))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a broken hook must never block the loop
                print(
                    f"[hook {rule.name}] {event.value} failed: {exc}",
                    file=sys.stderr,
                )
                await self._release_once(rule)
                continue
            if outcome.err is not None:
                print(
                    f"[hook {rule.name}] {event.value} failed: {outcome.err}",
                    file=sys.stderr,
                )
                await self._release_once(rule)
                continue
            if outcome.prompt:
                result.injected_prompts.append(outcome.prompt)
            if outcome.blocked and is_blocking(event):
                result.blocked = True
                result.reason = outcome.reason
                result.blocking_hook_name = rule.name
                break
        return result

    async def _run_async(self, rule: Rule, payload: Payload) -> None:
        try:
            outcome = await self._executor.run(rule, payload, blocking=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - background hook must not crash
            outcome = None
            print(
                f"[hook {rule.name}] {rule.event.value} failed: {exc}",
                file=sys.stderr,
            )
        if outcome is not None and outcome.err is not None:
            print(
                f"[hook {rule.name}] {rule.event.value} failed: {outcome.err}",
                file=sys.stderr,
            )
            await self._release_once(rule)

    async def _claim_once(self, rule: Rule) -> bool:
        async with self._lock:
            if rule.name in self._once_fired:
                return False
            self._once_fired.add(rule.name)
            return True

    async def _release_once(self, rule: Rule) -> None:
        if not rule.only_once:
            return
        async with self._lock:
            self._once_fired.discard(rule.name)

    def reset_for_new_session(self) -> None:
        # /clear、/resume 切新会话：only_once 重新生效。
        self._once_fired.clear()

    def cancel_pending(self) -> None:
        for task in list(self._tasks):
            task.cancel()

    async def aclose(self) -> None:
        self.cancel_pending()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._executor.aclose()

    @property
    def sources(self) -> list[str]:
        return list(self._sources)

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)


__all__ = ["DispatchResult", "Engine"]
