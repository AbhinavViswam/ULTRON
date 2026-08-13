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

    def __init__(self, speaker, echo_to_console: bool = True):
        self.speaker = speaker
        self.echo_to_console = echo_to_console
        self._queue = queue.Queue()
        self._running = True
        self._listeners = []
        self._speaking_listeners = []
        self._consumer_thread = threading.Thread(target=self._consumer_loop, daemon=True)
        self._consumer_thread.start()

    def on_message(self, callback):
        """Registers a callback invoked as each message is spoken.

        Signature: callback(text, source). Lets a GUI render messages as they
        surface without the console being the only output path. Callbacks run
        on the consumer thread, so a UI must marshal to its own thread.
        """
        self._listeners.append(callback)
        return callback

    def on_speaking_changed(self, callback):
        """Registers callback(is_speaking), bracketing each spoken message."""
        self._speaking_listeners.append(callback)
        return callback

    def _emit_speaking(self, speaking: bool):
        for callback in list(self._speaking_listeners):
            try:
                callback(speaking)
            except Exception as e:
                print(f"[OutputManager] Speaking listener error: {e}")

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

            if should_print and self.echo_to_console:
                print(f"\nUltron: {text}")

            for callback in list(self._listeners):
                try:
                    callback(text, source)
                except Exception as e:
                    print(f"[OutputManager] Listener error: {e}")

            # Speak synchronously — blocks until finished or interrupted
            # Capture the current speech_id; if it changes mid-speech the
            # speaker will abort automatically.
            current_id = self.speaker.speech_id
            self._emit_speaking(True)
            try:
                self.speaker.speak(text, current_id)
            finally:
                self._emit_speaking(False)
