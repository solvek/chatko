import asyncio
from pathlib import Path

from chatko.infrastructure.watcher import watch_files


async def test_a_change_of_a_watched_file_calls_back_and_other_files_do_not(
    tmp_path: Path,
) -> None:
    config, script, other = tmp_path / "chatko.yaml", tmp_path / "routing.py", tmp_path / "x.txt"
    config.write_text("a: 1\n", encoding="utf-8")
    changed = asyncio.Event()
    calls = 0

    async def on_change() -> None:
        nonlocal calls
        calls += 1
        changed.set()

    stop = asyncio.Event()
    watcher = asyncio.create_task(watch_files([config, script], on_change, stop, debounce_ms=50))
    await asyncio.sleep(0.5)  # let the watcher start
    # macOS (FSEvents) may replay the creation of config just before the watching began
    settled = calls

    other.write_text("ignored", encoding="utf-8")
    await asyncio.sleep(0.5)
    assert calls == settled

    script.write_text("def route(msg, ctx): ...\n", encoding="utf-8")  # created later
    changed.clear()
    await asyncio.wait_for(changed.wait(), 10)
    stop.set()
    await asyncio.wait_for(watcher, 10)

    assert calls > settled


async def test_an_error_in_the_callback_does_not_end_the_watching(tmp_path: Path) -> None:
    config = tmp_path / "chatko.yaml"
    config.write_text("a: 1\n", encoding="utf-8")
    seen = asyncio.Event()
    calls = 0

    async def on_change() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first time fails")
        seen.set()

    stop = asyncio.Event()
    watcher = asyncio.create_task(watch_files([config], on_change, stop, debounce_ms=50))
    await asyncio.sleep(0.5)
    config.write_text("a: 2\n", encoding="utf-8")
    await asyncio.sleep(1)
    config.write_text("a: 3\n", encoding="utf-8")
    await asyncio.wait_for(seen.wait(), 10)
    stop.set()
    await asyncio.wait_for(watcher, 10)

    assert calls >= 2
