import queue
import threading


class OutputManager:
    """Centralized priority-aware output queue that coordinates speech between
    user responses, cron notifications, and system messages.

    Priority levels:
        - "user"   : Highest. Triggers interrupt of current speech.
        - "system" : Medium. Loading phrases, welcome messages.
        - "cron"   : Lowest. Queued notifications. Cleared on user interrupt.
    """

    def __init__(self, speaker):
        self.speaker = speaker
        self._queue = queue.Queue()
        self._running = True
        self._consumer_thread = threading.Thread(target=self._consumer_loop, daemon=True)
        self._consumer_thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, message: str, source: str = "system", print_msg: bool = True):
        """Adds a message to the output queue.

        Args:
            message:   The text to be spoken.
            source:    One of "user", "system", or "cron".
            print_msg: Whether to print the message to the console.
        """
        self._queue.put({"text": message, "source": source, "print": print_msg})

    def interrupt(self):
        """Immediately stops current speech and discards all pending cron
        messages from the queue. Called when the user gives a new command."""
        # 1. Kill active audio
        self.speaker.stop()

        # 2. Drain low-priority (cron) items from the queue, keep user/system
        kept = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item["source"] != "cron":
                kept.append(item)

        # Re-enqueue any non-cron items we want to keep
        for item in kept:
            self._queue.put(item)

    def stop(self):
        """Shuts down the consumer thread cleanly."""
        self._running = False
        self.speaker.stop()
        # Push a sentinel so the blocking get() unblocks
        self._queue.put(None)

    # ------------------------------------------------------------------
    # Internal consumer
    # ------------------------------------------------------------------

    def _consumer_loop(self):
        """Background thread that pulls messages from the queue and speaks
        them one at a time, blocking until each finishes or is interrupted."""
        while self._running:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # Sentinel check for shutdown
            if item is None:
                break

            text = item["text"]
            source = item["source"]
            should_print = item["print"]

            if not text or not text.strip():
                continue

            if should_print:
                print(f"\nUltron: {text}")

            # Speak synchronously — blocks until finished or interrupted
            # Capture the current speech_id; if it changes mid-speech the
            # speaker will abort automatically.
            current_id = self.speaker.speech_id
            self.speaker.speak(text, current_id)
