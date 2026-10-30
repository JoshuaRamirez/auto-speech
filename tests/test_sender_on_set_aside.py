"""A line on the missed list carries its sender (#m1030-1058-d1ad).

On 2026-10-30 at 10:53 the speech control's queue listed desk lines with no
sender ("-") beside others from the same desk named "Master Desk". Those lines
had been handed to the missed list by desk-speaker (`missed add --source
"desk-say <file>"`), which sent no session, and then played again by his Play:
the replay's queue item had no session, so neither the queue nor the history
nor the spoken prefix could name the desk; the control's missed row showed the
caller's label "desk-say <file>", not a sender.

The sender is recorded from the line itself, when it is first known: the
session the line came with, resolved to its desk name the same way the history
does, at the moment the line is set aside or added. It travels with the line:
the missed entry, its replay's queue item, the queue view, the control's row,
and the history when it plays. Nothing is guessed: a line with no session and
no named sender stays unnamed.

Nothing is heard (a recording sink); everything is in a temporary folder."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
for _p in (str(SRC), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("AUTO_SPEECH_TMP_ROOT", tempfile.mkdtemp(prefix="auto-speech-tests-"))

import unittest  # noqa: E402

import cli  # noqa: E402
from daemon_client import DaemonClient  # noqa: E402
from line_ledger import DONE  # noqa: E402
from missed_speech import REASON_ADDED, REASON_STOPPED, MissedLine, play_ids  # noqa: E402
from priority_arbiter import QueueItem  # noqa: E402
from speech_control import MISSED_PREFIX, ControlDriver  # noqa: E402
from speech_history import HistoryStore, SpeechHistory  # noqa: E402
from test_line_id import _Daemon  # noqa: E402

DESK_SESSION = "24d3f660-9870-4883-a486-49a0c47941dc"
NAMES = {DESK_SESSION: "Master Desk"}
FILE = "2026-10-30-104426.141-desk"


class _Named(_Daemon):
    synth_s = 0.02

    def service(self):
        svc = super().service()
        self.names = dict(NAMES)
        svc._speaker.name_for.side_effect = lambda sid: self.names.get(sid or "")
        svc._history = SpeechHistory(self.tmp / "history", store=HistoryStore(self.tmp / "history.toml"))
        return svc

    def control(self) -> ControlDriver:
        return ControlDriver(HistoryStore(self.tmp / "history.toml"), history=self.svc._history, missed=self.svc._missed,
                             actions=self.svc._control_actions(), installed=lambda: False, icon=None,
                             start_worker=False, names=self.svc._sender_name)

    def missed_row(self, entry_id: str) -> dict:
        return next(i for i in self.control().items() if i["id"] == MISSED_PREFIX + entry_id)


class TestSetAsideCarriesItsSender(_Named):
    def test_a_desk_line_set_aside_on_his_stop_records_its_sender(self):
        self.svc._stop_speech(by="test")
        self.svc.dispatch_json({"action": "speak", "text": "Finding so far on the blank window.", "priority": 2,
                                "line_id": FILE, "session_id": DESK_SESSION})
        self.wait_for(lambda: len(self.svc._missed.load()) == 1, 5, self.svc._missed.load)
        (m,) = self.svc._missed.load()
        self.assertEqual(m.reason, REASON_STOPPED)
        self.assertEqual((m.session_id, m.sender), (DESK_SESSION, "Master Desk"), "recorded when set aside")

        self.names.clear()  # the desk's note later closed: the record still names it
        self.assertEqual(self.missed_row(m.id).get("source"), "Master Desk")

    def test_a_line_with_no_session_stays_unnamed(self):
        self.svc._stop_speech(by="test")
        self.svc.dispatch_json({"action": "speak", "text": "From nobody.", "priority": 2, "line_id": "anon"})
        self.wait_for(lambda: len(self.svc._missed.load()) == 1, 5, self.svc._missed.load)
        self.assertIsNone(self.svc._missed.load()[0].sender, "no session, no sender: never guessed")


class TestMissedAddCarriesItsSender(_Named):
    def add(self, **extra) -> dict:
        return self.svc.dispatch_json({"action": "missed_add", "text": "On the row highlight.",
                                       "source": f"desk-say {FILE}.txt", "line_id": FILE,
                                       "origin_file": f"{FILE}.txt", **extra})

    def test_desk_speakers_hand_off_with_its_session_names_the_desk(self):
        reply = self.add(session_id=DESK_SESSION)
        self.assertEqual(reply["status"], "ok")
        (m,) = self.svc._missed.load()
        self.assertEqual(m.reason, REASON_ADDED)
        self.assertEqual((m.session_id, m.sender), (DESK_SESSION, "Master Desk"))
        self.assertEqual(self.missed_row(m.id).get("source"), "Master Desk", "the desk, not the caller's label")

    def test_a_named_sender_is_kept_as_given(self):
        self.add(sender="Personal Desk")
        self.assertEqual(self.svc._missed.load()[0].sender, "Personal Desk")

    def test_a_hand_off_without_a_session_still_shows_the_callers_label(self):
        self.add()
        (m,) = self.svc._missed.load()
        self.assertIsNone(m.sender)
        self.assertEqual(self.missed_row(m.id).get("source"), f"desk-say {FILE}.txt")


class TestTheReplayKeepsTheSender(_Named):
    """The 10:57 case: a missed line played again waits in the queue and plays
    under its sender's name, even when the name map no longer knows the
    session."""

    def test_queue_view_names_the_replay_from_its_record(self):
        q = QueueItem(priority=2, payload="On the row highlight.", session_id="closed-session", missed_id="e1",
                      sender="Master Desk")
        self.assertEqual(self.svc._queue_item_source(q), "Master Desk")
        q2 = QueueItem(priority=2, payload="Live.", session_id=DESK_SESSION)
        self.assertEqual(self.svc._queue_item_source(q2), "Master Desk", "a live line still names its session's desk")

    def test_a_missed_line_played_again_is_heard_under_its_sender(self):
        line = MissedLine(id="e1", text="On the row highlight.", queued_at=1.0, reason=REASON_ADDED,
                          session_id="closed-session", sender="Master Desk", line_id=FILE, origin_file=f"{FILE}.txt",
                          source=f"desk-say {FILE}.txt")
        self.svc._missed.miss(line, reason=REASON_ADDED)
        self.assertEqual(self.svc._play_missed_ids(["e1"]), 1)
        self.wait_for(lambda: self.line(FILE)["state"] == DONE, 5, lambda: self.line(FILE))
        self.wait_for(lambda: self.svc._history.load(), 5)
        heard = self.svc._history.load()[-1]
        self.assertEqual(heard.sidecar["sender"]["name"], "Master Desk")
        self.assertEqual(heard.sidecar["sender"]["session_id"], "closed-session")

    def test_play_ids_sends_the_sender_and_session_with_the_line(self):
        self.svc._missed.miss(MissedLine(id="e2", text="A line.", queued_at=1.0, session_id=DESK_SESSION,
                                         sender="Master Desk", line_id="L2"), reason=REASON_ADDED)
        client = MagicMock()
        play_ids(["e2"], missed=self.svc._missed, client=client)
        kw = client.speak.call_args.kwargs
        self.assertEqual((kw["session_id"], kw["sender"]), (DESK_SESSION, "Master Desk"))


class TestAnEndedSessionIsNamedFromTheHistory(_Named):
    """10-30: entry a046e614 (set aside 10-29 under the Master Desk's session
    24d3f660…) showed no sender: the desk had since restarted on a new
    session, so its note no longer named the old one. The history recorded
    that session as "Master Desk" 789 times: that record names it."""

    def test_a_session_the_notes_no_longer_name_is_named_by_its_history(self):
        self.speak_from_desk("A line heard earlier.", "heard-1")
        self.names.clear()  # the desk restarted: its note names a new session
        self.svc.__dict__.pop("_recorded_senders", None)
        self.svc.__dict__.pop("_recorded_senders_at", None)
        old = MissedLine(id="e3", text="Set aside yesterday.", queued_at=1.0, session_id=DESK_SESSION, line_id="L3")
        self.svc._missed.miss(old, reason=REASON_STOPPED)
        self.assertEqual(self.missed_row("e3").get("source"), "Master Desk")
        self.assertIsNone(self.svc._sender_name("never-heard-session"), "a session nothing recorded: no name")

    def speak_from_desk(self, text: str, line_id: str) -> None:
        self.svc.dispatch_json({"action": "speak", "text": text, "priority": 2, "line_id": line_id,
                                "session_id": DESK_SESSION})
        self.wait_for(lambda: self.line(line_id)["state"] == DONE, 5, lambda: self.line(line_id))
        self.wait_for(lambda: self.svc._history.load(), 5)


class TestTheCliAndClientCarryTheSession(unittest.TestCase):
    def test_missed_add_session_id_reaches_the_daemon(self):
        client = MagicMock()
        client.missed_add.return_value = MagicMock(status="ok", raw={"id": "e1"})
        args = cli.build_parser().parse_args(
            ["missed", "add", "--text", "A line.", "--source", "desk-say x.txt", "--session-id", DESK_SESSION])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.handle_missed(args, client=client), 0)
        self.assertEqual(client.missed_add.call_args.kwargs["session_id"], DESK_SESSION)

    def test_daemon_client_puts_the_session_and_sender_in_the_payload(self):
        c = DaemonClient.__new__(DaemonClient)
        sent = []
        c._send_request = lambda payload: sent.append(payload) or MagicMock(status="ok")
        c.missed_add("A line.", session_id=DESK_SESSION, sender="Master Desk")
        self.assertEqual((sent[0]["session_id"], sent[0]["sender"]), (DESK_SESSION, "Master Desk"))
        c.speak("A line.", session_id=DESK_SESSION, sender="Master Desk", line_id="L")
        self.assertEqual(sent[-1]["sender"], "Master Desk")


if __name__ == "__main__":
    unittest.main(verbosity=2)
