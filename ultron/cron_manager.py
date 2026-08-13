import time
import ctypes
import threading
import datetime
from ultron.config import config
from ultron.plugins.gmail_plugin import get_unread_emails_count
from ultron.plugins.notification_plugin import send_toast

class CronManager:
    """Manages scheduled background cron tasks for Ultron."""
    
    def __init__(self, brain=None, speaker=None, output_manager=None):
        self.brain = brain
        self.speaker = speaker
        self.output_manager = output_manager
        self.running = False
        self.thread = None
        self.last_runs = {}
        
        # Registry of executable cron actions
        self.actions = {
            "unread_emails_check": self._action_unread_emails_check,
        }
        
    def register_action(self, name: str, func):
        """Registers custom cron action functions dynamically.
        Args:
            name: The key used in settings.json under cron_jobs.
            func: Function taking (job_config) as argument.
        """
        self.actions[name] = func
        
    def _speak(self, message: str):
        """Routes speech through the OutputManager queue if available,
        otherwise falls back to direct speaker."""
        if self.output_manager:
            self.output_manager.enqueue(message, source="cron")
        elif self.speaker:
            print(f"\nUltron: {message}")
            self.speaker.speak_async(message)

    def _action_unread_emails_check(self, job_config):
        """Cron action to check Gmail for unread emails and read out notification."""
        try:
            count = get_unread_emails_count()
            now_str = datetime.datetime.now().strftime('%H:%M:%S')
            print(f"\n[Cron Job] Unread Email Check ({now_str}): Found {count} unread email(s).")
            
            if count > 0:
                spoken_msg = f"Sir, there are {count} unread emails, would you like to take a look?"
                self._speak(spoken_msg)
                    
                if job_config.get("notify_popup", False):
                    send_toast("Ultron Email Alert", f"Sir, you have {count} unread email(s).")
        except Exception as e:
            print(f"[Cron Error] Failed unread email check: {e}")
            
    def _load_cron_jobs(self):
        """Loads cron_jobs configuration. Config reloads itself on disk change,
        so edits made by hand or through the UI are picked up each tick."""
        return config.get("cron_jobs", {}) or {}

    def _loop(self):
        """Main background scheduler loop checking job intervals."""
        while self.running:
            cron_jobs = self._load_cron_jobs()
            now = time.time()
            
            for job_name, job_config in cron_jobs.items():
                if not isinstance(job_config, dict) or not job_config.get("enabled", True):
                    continue
                    
                interval = job_config.get("interval_seconds", 3600)
                last_run = self.last_runs.get(job_name, 0)
                
                # Check if interval has elapsed
                if now - last_run >= interval:
                    self.last_runs[job_name] = now
                    if job_name in self.actions:
                        # Spawn non-blocking thread for the job execution
                        t = threading.Thread(target=self.actions[job_name], args=(job_config,), daemon=True)
                        t.start()
                    else:
                        print(f"[Cron Warning] Unknown cron action '{job_name}' configured in settings.json.")
                        
            time.sleep(5)  # Check every 5 seconds

    def start(self):
        """Starts the cron manager background thread."""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
            print("Ultron Cron Manager initialized and running in background.")

    def stop(self):
        """Stops the cron manager background thread."""
        self.running = False
