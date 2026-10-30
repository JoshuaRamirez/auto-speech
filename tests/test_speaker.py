#!/usr/bin/env python3
"""Speaker names: 'Master Desk here,' only when who is talking changes.

His 19:06 (via Clerk #m1018-1908-8c7b): the say tool should not keep saying
"Master Desk" when the same desk is the only one talking, and must name the
desk when another one starts talking. Names come from the desk agents' notes
(front matter `session:` → `name:` or the note's file name); the prefix is
spoken only when the speaker differs from the last one spoken, or after a
quiet gap; a line that already opens with its own "<name> here," is not
doubled.

Hermetic: a temp agents dir with hand-written notes, a temp config file, an
injected clock. Nothing reads his real desk tree.
"""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import os
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "plugin" / "scripts" / "python"
sys.path.insert(0, str(PLUGIN_ROOT))

os.environ.setdefault("AUTO_SPEECH_TMP_ROOT", tempfile.mkdtemp(prefix="auto-speech-speaker-"))

from speaker import (  # noqa: E402
    ACTIONS,
    DEFAULT_QUIET_GAP_SECONDS,
    UNKNOWN_NAME,
    Announcer,
    DeskNames,
    SpeakerSettings,
    SpeakerStore,
    is_self_announced,
    run_action,
)

MASTER = "aaaaaaaa-0000-4000-8000-000000000000"
ASPEN = "bbbbbbbb-0000-4000-8000-000000000001"
DEV = "cccccccc-0000-4000-8000-000000000002"
CLERK = "dddddddd-0000-4000-8000-000000000003"
PERSONAL = "eeeeeeee-0000-4000-8000-000000000004"
STRANGER = "00000000-0000-4000-8000-00000000beef"


def _note(path: Path, **props: str) -> None:
    body = "\n".join(f"{k}: {v}" for k, v in props.items())
    path.write_text(f"---\n{body}\n---\nHerdr tab **x**.\n", encoding="utf-8")


def _agents_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="auto-speech-agents-"))
    _note(d / "Desk.md", type="agent", name='"Master Desk"', state="working", session=MASTER,
          section='"[[Desk/AspenESS|AspenESS]]"', herdr_session="default", role="head")
    _note(d / "First Desk.md", type="agent", state="closed", session=MASTER,
          section='"[[Desk/AspenESS|AspenESS]]"')
    _note(d / "Aspen Desk.md", type="agent", state="done", session=ASPEN,
          section='"[[Desk/AspenESS|AspenESS]]"', role="desk")
    _note(d / "Development Desk.md", type="agent", state="idle", session=DEV,
          section='"[[Desk/AspenESS|AspenESS]]"')
    _note(d / "Clerk - Desk.md", type="agent", state="working", session=CLERK,
          section='"[[Desk/AspenESS|AspenESS]]"', role="assistant")
    _note(d / "Personal - Finance.md", type="agent", state="done", session=PERSONAL,
          section='"[[Personal]]"')
    (d / "notes.txt").write_text("not a note", encoding="utf-8")
    return d


class _Clock:
    def __init__(self, t: float = 1_700_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class TestDeskNames(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = _agents_dir()
        self.names = DeskNames(self.dir, now=_Clock())

    def test_the_desk_core_names_resolve_from_the_notes(self) -> None:
        self.assertEqual(self.names.name_for(MASTER), "Master Desk")
        self.assertEqual(self.names.name_for(ASPEN), "Aspen Desk")
        self.assertEqual(self.names.name_for(DEV), "Development Desk")

    def test_any_other_agent_is_named_by_its_note(self) -> None:
        self.assertEqual(self.names.name_for(CLERK), "Clerk - Desk")

    def test_a_working_note_wins_over_a_closed_one_on_the_same_session(self) -> None:
        # Desk (working, name: Master Desk) and First Desk (closed) share a session.
        self.assertEqual(self.names.name_for(MASTER), "Master Desk")

    def test_a_personal_note_is_never_read_into_the_map(self) -> None:
        self.assertIsNone(self.names.name_for(PERSONAL))
        self.assertNotIn("Personal - Finance", self.names.all().values())

    def test_an_unknown_session_has_no_name(self) -> None:
        self.assertIsNone(self.names.name_for(STRANGER))
        self.assertIsNone(self.names.name_for(""))
        self.assertIsNone(self.names.name_for(None))

    def test_a_new_note_is_seen_after_the_refresh_interval(self) -> None:
        clock = _Clock()
        names = DeskNames(self.dir, now=clock, refresh_seconds=30.0)
        self.assertIsNone(names.name_for(STRANGER))
        _note(self.dir / "Stranger.md", type="agent", state="working", session=STRANGER,
              section='"[[Desk/AspenESS|AspenESS]]"')
        self.assertIsNone(names.name_for(STRANGER), "cached within the interval")
        clock.t += 31.0
        self.assertEqual(names.name_for(STRANGER), "Stranger")

    def test_an_override_wins_over_the_notes(self) -> None:
        names = DeskNames(self.dir, overrides={CLERK: "The Clerk", STRANGER: "Side Desk"}, now=_Clock())
        self.assertEqual(names.name_for(CLERK), "The Clerk")
        self.assertEqual(names.name_for(STRANGER), "Side Desk")

    def test_a_missing_agents_dir_is_an_empty_map(self) -> None:
        names = DeskNames(Path(tempfile.mkdtemp()) / "nowhere", now=_Clock())
        self.assertIsNone(names.name_for(MASTER))
        self.assertEqual(names.count(), 0)


class TestSelfAnnounced(unittest.TestCase):
    NAMES = ("Master Desk", "Clerk - Desk", "Aspen Desk")

    def test_a_line_that_opens_with_a_known_name_here_is_self_announced(self) -> None:
        for text in ("Master Desk here, the build is green.", "master desk here: done.",
                     "  Clerk - Desk here. Two things.", "Aspen Desk here — one thing."):
            self.assertTrue(is_self_announced(text, self.NAMES), text)

    def test_other_lines_are_not(self) -> None:
        for text in ("The build is green.", "Here is the build.", "I am here, finally.",
                     "Someone else here, with news.",
                     "Nobody is here, and that is the point of this long opening clause here,"):
            self.assertFalse(is_self_announced(text, self.NAMES), text)


class TestAnnouncer(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.dir = _agents_dir()
        self.cfg = Path(tempfile.mkdtemp(prefix="auto-speech-speaker-cfg-")) / "speaker.toml"
        self.store = SpeakerStore(self.cfg)
        self.names = DeskNames(self.dir, now=self.clock)
        self.a = Announcer(self.store, self.names, now=self.clock)

    def _say(self, text: str, sid: str | None) -> str:
        plan = self.a.plan(text, sid)
        self.a.spoken(sid)
        return plan.text

    def test_the_first_speaker_is_named_and_a_repeat_is_not(self) -> None:
        self.assertEqual(self._say("The build is green.", MASTER), "Master Desk here, The build is green.")
        self.assertEqual(self._say("Tests next.", MASTER), "Tests next.")
        self.assertEqual(self._say("And docs.", MASTER), "And docs.")

    def test_a_change_of_speaker_is_named_each_way(self) -> None:
        self._say("One.", MASTER)
        self.assertEqual(self._say("Two.", ASPEN), "Aspen Desk here, Two.")
        self.assertEqual(self._say("Three.", ASPEN), "Three.")
        self.assertEqual(self._say("Four.", MASTER), "Master Desk here, Four.")

    def test_a_quiet_gap_names_the_same_speaker_again(self) -> None:
        self._say("One.", MASTER)
        self.clock.t += DEFAULT_QUIET_GAP_SECONDS - 1
        self.assertEqual(self._say("Two.", MASTER), "Two.")
        self.clock.t += DEFAULT_QUIET_GAP_SECONDS + 1
        self.assertEqual(self._say("Three.", MASTER), "Master Desk here, Three.")

    def test_the_gap_is_the_configured_one(self) -> None:
        self.store.set_quiet_gap(60)
        self._say("One.", MASTER)
        self.clock.t += 61
        self.assertEqual(self._say("Two.", MASTER), "Master Desk here, Two.")

    def test_a_line_that_names_itself_is_not_doubled(self) -> None:
        self.assertEqual(self._say("Master Desk here, hello.", MASTER), "Master Desk here, hello.")
        self.assertEqual(self._say("Next.", MASTER), "Next.", "the self-announced line counted as spoken by that desk")
        self._say("Aspen Desk here, one thing.", ASPEN)
        self.assertEqual(self._say("Back.", MASTER), "Master Desk here, Back.")

    def test_an_unknown_session_is_still_marked_as_a_change(self) -> None:
        self._say("One.", MASTER)
        self.assertEqual(self._say("Two.", STRANGER), f"{UNKNOWN_NAME} here, Two.")
        self.assertEqual(self._say("Three.", STRANGER), "Three.")
        self.assertEqual(self._say("Four.", MASTER), "Master Desk here, Four.")

    def test_a_line_with_no_session_is_left_alone_and_changes_nothing(self) -> None:
        self._say("One.", MASTER)
        self.assertEqual(self._say("Plain shell line.", None), "Plain shell line.")
        self.assertEqual(self._say("Plain shell line.", ""), "Plain shell line.")
        self.assertEqual(self._say("Two.", MASTER), "Two.")

    def test_only_a_spoken_line_moves_the_speaker(self) -> None:
        self.a.plan("One.", MASTER)  # planned, then held and set aside: never spoken
        self.assertEqual(self.a.plan("Two.", MASTER).text, "Master Desk here, Two.")

    def test_switched_off_the_text_is_untouched(self) -> None:
        self.store.set_enabled(False)
        self.assertEqual(self._say("One.", MASTER), "One.")
        self.assertEqual(self._say("Two.", ASPEN), "Two.")

    def test_the_plan_says_who_and_the_prefix_alone_for_cached_audio(self) -> None:
        plan = self.a.plan("One.", ASPEN)
        self.assertEqual(plan.name, "Aspen Desk")
        self.assertEqual(plan.prefix, "Aspen Desk here,")
        self.assertEqual(plan.clip_text, "Aspen Desk here.")
        self.a.spoken(ASPEN)
        plan = self.a.plan("Two.", ASPEN)
        self.assertIsNone(plan.prefix)
        self.assertEqual(plan.text, "Two.")

    def test_status_names_the_last_speaker(self) -> None:
        self.assertEqual(self.a.status()["last_speaker"], None)
        self._say("One.", MASTER)
        st = self.a.status()
        self.assertEqual(st["last_speaker"], "Master Desk")
        self.assertEqual(st["last_session_id"], MASTER)
        self.assertEqual(st["last_spoken_at"], self.clock.t)
        self.assertTrue(st["enabled"])
        self.assertEqual(st["quiet_gap_seconds"], DEFAULT_QUIET_GAP_SECONDS)


class TestSettingsAndStore(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Path(tempfile.mkdtemp(prefix="auto-speech-speaker-cfg-")) / "speaker.toml"
        self.store = SpeakerStore(self.cfg)

    def test_defaults_without_a_file(self) -> None:
        s = self.store.load()
        self.assertTrue(s.enabled)
        self.assertEqual(s.quiet_gap_seconds, DEFAULT_QUIET_GAP_SECONDS)
        self.assertEqual(s.names, ())
        self.assertEqual(s.problems, ())

    def test_round_trip(self) -> None:
        self.store.set_enabled(False)
        self.store.set_quiet_gap(120)
        self.store.set_name(CLERK, "The Clerk")
        s = SpeakerStore(self.cfg).load()
        self.assertFalse(s.enabled)
        self.assertEqual(s.quiet_gap_seconds, 120.0)
        self.assertEqual(dict(s.names), {CLERK: "The Clerk"})
        self.store.forget_name(CLERK)
        self.assertEqual(SpeakerStore(self.cfg).load().names, ())

    def test_bad_values_fall_back_and_are_listed(self) -> None:
        self.cfg.write_text(
            '[speaker]\nenabled = "yes"\nquiet_gap_seconds = -5\nagents_dir = 3\n[speaker.names]\nx = 1\n',
            encoding="utf-8",
        )
        s = self.store.load()
        self.assertTrue(s.enabled)
        self.assertEqual(s.quiet_gap_seconds, DEFAULT_QUIET_GAP_SECONDS)
        self.assertEqual(s.names, ())
        self.assertEqual(len(s.problems), 4, s.problems)

    def test_from_mapping_reads_agents_dir(self) -> None:
        s = SpeakerSettings.from_mapping({"agents_dir": "~/x/Agents"})
        self.assertEqual(s.agents_dir, "~/x/Agents")


class TestRunAction(unittest.TestCase):
    """One verb for `auto-speech speaker …` and the MCP tool `speaker`."""

    def setUp(self) -> None:
        self.cfg = Path(tempfile.mkdtemp(prefix="auto-speech-speaker-cfg-")) / "speaker.toml"
        self.store = SpeakerStore(self.cfg)
        self.names = DeskNames(_agents_dir(), now=_Clock())

    def test_actions(self) -> None:
        self.assertEqual(ACTIONS, ("status", "on", "off", "gap", "name", "forget", "who"))

    def test_status_text(self) -> None:
        r = run_action("status", store=self.store, names=self.names)
        self.assertTrue(r.ok)
        self.assertIn("speaker names: on", r.text)
        self.assertIn("quiet gap: 15 min", r.text)
        self.assertIn("desks known: 4", r.text)  # Desk and First Desk share a session; the personal note is skipped

    def test_off_and_on(self) -> None:
        self.assertIn("speaker names: off", run_action("off", store=self.store, names=self.names).text)
        self.assertFalse(self.store.load().enabled)
        self.assertIn("speaker names: on", run_action("on", store=self.store, names=self.names).text)

    def test_gap(self) -> None:
        r = run_action("gap", seconds=300, store=self.store, names=self.names)
        self.assertTrue(r.ok)
        self.assertIn("quiet gap: 5 min", r.text)
        self.assertEqual(self.store.load().quiet_gap_seconds, 300.0)
        self.assertFalse(run_action("gap", store=self.store, names=self.names).ok)
        self.assertFalse(run_action("gap", seconds=-1, store=self.store, names=self.names).ok)

    def test_name_and_forget(self) -> None:
        r = run_action("name", session_id=STRANGER, name="Side Desk", store=self.store, names=self.names)
        self.assertTrue(r.ok)
        self.assertIn("Side Desk", r.text)
        self.assertEqual(dict(self.store.load().names), {STRANGER: "Side Desk"})
        self.assertFalse(run_action("name", session_id=STRANGER, store=self.store, names=self.names).ok)
        r = run_action("forget", session_id=STRANGER, store=self.store, names=self.names)
        self.assertTrue(r.ok)
        self.assertEqual(self.store.load().names, ())

    def test_who(self) -> None:
        r = run_action("who", session_id=MASTER, store=self.store, names=self.names)
        self.assertTrue(r.ok)
        self.assertIn("Master Desk", r.text)
        r = run_action("who", session_id=STRANGER, store=self.store, names=self.names)
        self.assertTrue(r.ok)
        self.assertIn(UNKNOWN_NAME, r.text)
        self.assertFalse(run_action("who", store=self.store, names=self.names).ok)

    def test_unknown_action(self) -> None:
        self.assertFalse(run_action("dance", store=self.store, names=self.names).ok)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if result.wasSuccessful():
        print(f"speaker: {result.testsRun} tests passed")
        return 0
    print(f"speaker: {len(result.failures) + len(result.errors)} of {result.testsRun} failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
