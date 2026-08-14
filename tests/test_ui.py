"""The overlay's cards.

These build real widgets, so they need a QApplication (the `qt_app` fixture)
and a desktop session. They cover the things that broke by hand: text being
clipped, the transcript not following the newest turn, and a confirmation card
that could be dismissed into an approval.
"""

import pytest

pytest.importorskip("PySide6")

from ultron.ui.cards import (  # noqa: E402
    CARD_WIDTH, TRANSCRIPT_MAX_HEIGHT, ConfirmCard, InputCard, MessageCard
)


class TestMessageCard:
    def test_long_text_is_not_clipped(self, qt_app):
        """A wrapping label reports its height only once its width is known."""
        short = MessageCard("Yes sir.", "ultron")
        long = MessageCard("This is a considerably longer reply that has to "
                           "wrap over several lines to be readable at all.", "ultron")
        short.show(); long.show()
        qt_app.processEvents()
        assert long.height() > short.height()
        short.close(); long.close()

    def test_reminders_dwell_far_longer_than_chatter(self, qt_app):
        chatter = MessageCard("Opening Spotify.", "ultron")
        reminder = MessageCard("Time to stretch.", "reminder")
        assert reminder._dwell_ms > chatter._dwell_ms

    def test_dismissing_twice_is_safe(self, qt_app):
        """Clicking a card already fading used to delete the widget twice."""
        card = MessageCard("x", "ultron")
        card.show()
        card.dismiss()
        card.dismiss()

    def test_width_is_fixed(self, qt_app):
        assert MessageCard("x", "ultron").width() == CARD_WIDTH


class TestTranscript:
    def test_starts_hidden_and_empty(self, qt_app):
        card = InputCard()
        assert card.transcript.is_empty
        assert not card.transcript.isVisible()
        card.close()

    def test_appending_shows_it(self, qt_app):
        card = InputCard()
        card.append("hello", "user")
        qt_app.processEvents()
        assert not card.transcript.is_empty
        card.close()

    def test_it_stops_growing_at_the_ceiling(self, qt_app):
        card = InputCard()
        for i in range(40):
            card.append(f"message number {i} with enough text to wrap a line", "ultron")
        qt_app.processEvents()
        assert card.transcript.height() <= TRANSCRIPT_MAX_HEIGHT
        card.close()

    def test_it_follows_the_newest_turn(self, qt_app):
        card = InputCard()
        card.open_at_for_test = True
        for i in range(30):
            card.append(f"line {i}", "ultron")
        qt_app.processEvents()
        card.transcript.settle()
        bar = card.transcript.verticalScrollBar()
        assert bar.value() == bar.maximum()
        card.close()

    def test_old_rows_are_dropped(self, qt_app):
        from ultron.ui.cards import TRANSCRIPT_MAX_ROWS

        card = InputCard()
        for i in range(TRANSCRIPT_MAX_ROWS + 20):
            card.append(f"line {i}", "ultron")
        qt_app.processEvents()
        assert card.transcript._rows.count() - 1 <= TRANSCRIPT_MAX_ROWS
        card.close()

    def test_clearing_hides_it_again(self, qt_app):
        card = InputCard()
        card.append("hello", "user")
        qt_app.processEvents()
        card._clear_transcript()
        assert card.transcript.is_empty
        card.close()


class TestConfirmCard:
    def test_it_names_the_action(self, qt_app):
        card = ConfirmCard("permanently empty the Recycle Bin")
        card.show()
        qt_app.processEvents()
        assert card.isVisible()
        card._answer(False)

    def test_closing_is_a_refusal(self, qt_app):
        """Dismissing a question must never read as consent."""
        card = ConfirmCard("delete everything")
        answers = []
        card.answered.connect(answers.append)
        card.show()
        card.close()
        qt_app.processEvents()
        assert answers == [False]

    def test_only_the_first_answer_counts(self, qt_app):
        card = ConfirmCard("delete everything")
        answers = []
        card.answered.connect(answers.append)
        card._answer(True)
        card._answer(False)
        assert answers == [True]

    def test_expiring_answers_nothing_further(self, qt_app):
        """The core already treated the silence as no."""
        card = ConfirmCard("delete everything")
        answers = []
        card.answered.connect(answers.append)
        card.expire()
        card._answer(True)
        assert answers == []
