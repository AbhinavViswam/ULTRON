import sqlite3
import os
import uuid
from datetime import datetime

try:
    import chromadb
except ImportError:
    chromadb = None

class Database:
    def __init__(self, db_path="data/ultron.db"):
        # Ensure the directory exists
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        full_db_path = os.path.join(base_dir, db_path)
        os.makedirs(os.path.dirname(full_db_path), exist_ok=True)
        self.db_path = full_db_path
        
        self._init_db()
        
        # Initialize ChromaDB Vector Database for semantic memory
        if chromadb is None:
            print("[Warning] chromadb not installed. Semantic memory will fail.")
            self.chroma_client = None
            self.memories_col = None
            self.chat_history_col = None
        else:
            chroma_dir = os.path.join(base_dir, "data", "chroma")
            os.makedirs(chroma_dir, exist_ok=True)
            self.chroma_client = chromadb.PersistentClient(path=chroma_dir)
            self.memories_col = self.chroma_client.get_or_create_collection(name="memories")
            self.chat_history_col = self.chroma_client.get_or_create_collection(name="chat_history")

    def _init_db(self):
        """Initialize the SQLite tables (for exact/chronological storage)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create chat_history table (for chronological loading)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    role TEXT NOT NULL,
                    message TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Check if session_id exists for existing databases to migrate
            cursor.execute("PRAGMA table_info(chat_history)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'session_id' not in columns:
                cursor.execute("ALTER TABLE chat_history ADD COLUMN session_id TEXT")
            
            # Create tasks table (for exact time/state queries)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    description TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    scheduled_for DATETIME,
                    frequency TEXT DEFAULT NULL,
                    until_date DATETIME DEFAULT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Check for existing tasks to migrate frequency and until_date
            cursor.execute("PRAGMA table_info(tasks)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'frequency' not in columns:
                cursor.execute("ALTER TABLE tasks ADD COLUMN frequency TEXT DEFAULT NULL")
            if 'until_date' not in columns:
                cursor.execute("ALTER TABLE tasks ADD COLUMN until_date DATETIME DEFAULT NULL")

            # Things the user means to do, as opposed to things Ultron
            # should say at a time. Deliberately not the tasks table: a
            # reminder fires once and is finished, while a todo stays pending
            # until it is done and can be overdue. Sharing the table would put
            # todos in front of the reminder loop, which would then read them
            # aloud at their due date - a different feature than the one asked
            # for.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    due_date DATETIME DEFAULT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    completed_at DATETIME DEFAULT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tool_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_name TEXT NOT NULL,
                    success BOOLEAN NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            self._init_routines(cursor)
            conn.commit()

    def save_message(self, session_id: str, role: str, message: str):
        """Save a message to both SQLite (chronological) and ChromaDB (semantic)."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 1. Save to SQLite
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO chat_history (session_id, role, message, timestamp)
                VALUES (?, ?, ?, ?)
            ''', (session_id, role, message, timestamp))
            conn.commit()
            
        # 2. Save to ChromaDB
        if self.chat_history_col:
            doc_id = str(uuid.uuid4())
            self.chat_history_col.add(
                documents=[message],
                metadatas=[{"role": role, "session_id": session_id, "timestamp": timestamp}],
                ids=[doc_id]
            )

    def record_tool_usage(self, tool_name: str, success: bool):
        """Records a single tool invocation into the chronological history."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO tool_usage (tool_name, success)
                VALUES (?, ?)
            ''', (tool_name, 1 if success else 0))
            conn.commit()

    def get_tool_statistics(self):
        """Returns aggregate usage statistics for all tools, sorted by total uses."""
        import json, os
        
        # 1. Get from SQLite
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    tool_name, 
                    COUNT(CASE WHEN success=1 THEN 1 END) as successes, 
                    COUNT(CASE WHEN success=0 THEN 1 END) as failures,
                    MAX(timestamp) as last_used
                FROM tool_usage 
                GROUP BY tool_name 
            ''')
            db_stats = {row[0]: {"successes": row[1], "failures": row[2], "last_used": row[3]} for row in cursor.fetchall()}

        # 2. Merge with legacy tool_usage.json
        json_path = os.path.join(os.path.dirname(self.db_path), "tool_usage.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    legacy_data = json.load(f)
                    
                for tool_name, stats in legacy_data.items():
                    calls = stats.get("calls", 0)
                    if calls == 0:
                        continue
                    errors = stats.get("errors", 0)
                    successes = calls - errors
                    last_used = stats.get("last_used")
                    
                    if tool_name in db_stats:
                        db_stats[tool_name]["successes"] += successes
                        db_stats[tool_name]["failures"] += errors
                        if last_used and (not db_stats[tool_name]["last_used"] or last_used > db_stats[tool_name]["last_used"]):
                            db_stats[tool_name]["last_used"] = last_used
                    else:
                        db_stats[tool_name] = {
                            "successes": successes,
                            "failures": errors,
                            "last_used": last_used
                        }
            except Exception:
                pass
                
        # 3. Format and sort
        results = []
        for name, stats in db_stats.items():
            results.append({
                "tool_name": name,
                "successes": stats["successes"],
                "failures": stats["failures"],
                "last_used": stats["last_used"]
            })
            
        results.sort(key=lambda x: (x["successes"] + x["failures"]), reverse=True)
        return results

    # --- todos ---------------------------------------------------------

    def add_todo(self, task: str, due_date: str = None) -> int:
        """Records something to do later. Returns its id.

        A due date is optional on purpose: most of what people mean to do has
        no deadline, and forcing one would turn every note into a reminder.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO todos (task, due_date) VALUES (?, ?)",
                           (task, due_date))
            conn.commit()
            return cursor.lastrowid

    def list_todos(self, include_done: bool = False) -> list:
        """Todos as dictionaries, oldest first, undated ones last.

        Ordering puts anything with a deadline before anything without, so
        the first few rows are the ones that actually matter today.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM todos"
            if not include_done:
                query += " WHERE status = 'pending'"
            query += " ORDER BY due_date IS NULL, due_date ASC, id ASC"
            return [dict(row) for row in conn.execute(query)]

    def find_todos(self, text: str, include_done: bool = False) -> list:
        """Pending todos whose text contains *text*, case-insensitively.

        Spoken commands name a todo rather than its number - "mark the bank
        call as done" - so matching by description is the normal path and the
        id is the fallback.
        """
        needle = (text or "").strip().lower()
        if not needle:
            return []
        return [row for row in self.list_todos(include_done=include_done)
                if needle in row["task"].lower()]

    def complete_todo(self, todo_id: int) -> bool:
        """Marks one done. False if there was no such pending todo."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE todos SET status = 'done', "
                "completed_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'pending'", (todo_id,))
            conn.commit()
            return cursor.rowcount > 0

    def reopen_todo(self, todo_id: int) -> bool:
        """Puts a completed todo back on the list."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE todos SET status = 'pending', completed_at = NULL "
                "WHERE id = ?", (todo_id,))
            conn.commit()
            return cursor.rowcount > 0

    def delete_todo(self, todo_id: int) -> bool:
        """Removes one outright. False if it was not there."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
            conn.commit()
            return cursor.rowcount > 0

    def add_task(self, description: str, scheduled_for: str = None, frequency: str = None, until_date: str = None):
        """Add a new task for scheduling."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO tasks (description, scheduled_for, frequency, until_date)
                VALUES (?, ?, ?, ?)
            ''', (description, scheduled_for, frequency, until_date))
            conn.commit()

    def get_pending_tasks(self):
        """Retrieve all pending tasks."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, description, scheduled_for, created_at, frequency, until_date
                FROM tasks 
                WHERE status = 'pending'
            ''')
            return cursor.fetchall()
            
    def update_task_time(self, task_id: int, new_scheduled_for: str):
        """Update the scheduled time for a recurring task."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE tasks 
                SET scheduled_for = ? 
                WHERE id = ?
            ''', (new_scheduled_for, task_id))
            conn.commit()
            
    def update_task_status(self, task_id: int, status: str):
        """Update the status of a specific task (e.g., 'completed')."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE tasks 
                SET status = ? 
                WHERE id = ?
            ''', (status, task_id))
            conn.commit()

    def delete_task(self, task_id: int):
        """Delete a task from the database completely."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM tasks 
                WHERE id = ?
            ''', (task_id,))
            conn.commit()
            
    def _init_routines(self, cursor):
        """Scheduled instructions. Separate from tasks: a reminder carries a
        sentence to say, a routine carries work to do."""
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS routines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                instruction TEXT NOT NULL,
                kind TEXT DEFAULT 'daily',
                days TEXT DEFAULT '',
                day_of_month INTEGER DEFAULT 0,
                every_n_days INTEGER DEFAULT 0,
                at_time TEXT DEFAULT '08:00',
                once_date TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                deliver TEXT DEFAULT 'speak,card',
                next_run TEXT,
                last_run TEXT,
                last_result TEXT DEFAULT '',
                fail_count INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

    @staticmethod
    def _routine_row(row) -> dict:
        (rid, name, instruction, kind, days, day_of_month, every_n_days,
         at_time, once_date, enabled, deliver, next_run, last_run,
         last_result, fail_count, created_at) = row
        return {
            "id": rid, "name": name, "instruction": instruction,
            "schedule": {
                "kind": kind,
                "days": [int(d) for d in days.split(",") if d.strip().isdigit()],
                "day_of_month": day_of_month or 0,
                "every_n_days": every_n_days or 0,
                "at_time": at_time or "08:00",
                "once_date": once_date or "",
            },
            "enabled": bool(enabled), "deliver": deliver or "speak,card",
            "next_run": next_run, "last_run": last_run,
            "last_result": last_result or "", "fail_count": fail_count or 0,
            "created_at": created_at,
        }

    def add_routine(self, name: str, instruction: str, schedule: dict,
                    next_run: str, deliver: str = "speak,card") -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO routines
                (name, instruction, kind, days, day_of_month, every_n_days,
                 at_time, once_date, next_run, deliver)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                name, instruction, schedule.get("kind", "daily"),
                ",".join(str(d) for d in schedule.get("days") or []),
                schedule.get("day_of_month") or 0,
                schedule.get("every_n_days") or 0,
                schedule.get("at_time", "08:00"),
                schedule.get("once_date", ""),
                next_run, deliver,
            ))
            conn.commit()
            return cursor.lastrowid

    def list_routines(self) -> list:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM routines ORDER BY id").fetchall()
        return [self._routine_row(row) for row in rows]

    def get_routine(self, routine_id: int):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()
        return self._routine_row(row) if row else None

    def update_routine(self, routine_id: int, **fields):
        """Updates named columns. Unknown names are ignored, not guessed at."""
        allowed = {
            "name", "instruction", "kind", "days", "day_of_month",
            "every_n_days", "at_time", "once_date", "enabled", "deliver",
            "next_run", "last_run", "last_result", "fail_count",
        }
        changes = {k: v for k, v in fields.items() if k in allowed}
        if not changes:
            return False
        assignments = ", ".join(f"{k} = ?" for k in changes)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"UPDATE routines SET {assignments} WHERE id = ?",
                         (*changes.values(), routine_id))
            conn.commit()
        return True

    def set_routine_schedule(self, routine_id: int, schedule: dict, next_run: str):
        return self.update_routine(
            routine_id,
            kind=schedule.get("kind", "daily"),
            days=",".join(str(d) for d in schedule.get("days") or []),
            day_of_month=schedule.get("day_of_month") or 0,
            every_n_days=schedule.get("every_n_days") or 0,
            at_time=schedule.get("at_time", "08:00"),
            once_date=schedule.get("once_date", ""),
            next_run=next_run,
        )

    def delete_routine(self, routine_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
            conn.commit()
            return cursor.rowcount > 0

    def save_memory(self, category: str, key: str, value: str, importance: int):
        """Save a new memory to the Vector DB."""
        if not self.memories_col:
            return

        doc_id = str(uuid.uuid4())
        # Combine into a single semantic string
        semantic_text = f"[{category}] {key}: {value}"
        self.memories_col.add(
            documents=[semantic_text],
            metadatas=[{
                "category": category,
                "key": key,
                "importance": importance,
                # Kept alongside the semantic document so a memory can be shown
                # and deleted without having to parse it back out of the text.
                "value": value,
                # Microseconds, not seconds: several memories are routinely
                # saved within the same second, and a tie here falls through
                # to the random uuid — which would shuffle the numbers the
                # user was just shown.
                "saved_at": datetime.now().isoformat(timespec="microseconds"),
            }],
            ids=[doc_id]
        )

    def list_memories(self) -> list:
        """Every saved memory, oldest first, as dicts.

        Ordered so the numbers a person is shown stay put between listing
        something and asking for it to be deleted.
        """
        if not self.memories_col:
            return []

        data = self.memories_col.get()
        documents = data.get("documents") or []
        metadatas = data.get("metadatas") or []
        ids = data.get("ids") or []

        memories = []
        for i, document in enumerate(documents):
            meta = (metadatas[i] if i < len(metadatas) else None) or {}
            value = meta.get("value")
            if value is None:
                # Saved before 'value' was stored separately: recover it from
                # the "[category] key: value" document.
                value = document.split(":", 1)[1].strip() if ":" in document else document
            memories.append({
                "id": ids[i],
                "category": meta.get("category", ""),
                "key": meta.get("key", ""),
                "value": value,
                "importance": meta.get("importance", 0),
                "saved_at": meta.get("saved_at", ""),
                "document": document,
            })

        # Entries predating 'saved_at' sort first, which puts the oldest known
        # memories at the top either way.
        memories.sort(key=lambda m: (m["saved_at"] or "", m["id"]))
        return memories

    def delete_memory(self, memory_id: str) -> bool:
        """Removes one memory by its id. True if it was there to remove."""
        if not self.memories_col:
            return False
        existing = self.memories_col.get(ids=[memory_id])
        if not (existing and existing.get("ids")):
            return False
        self.memories_col.delete(ids=[memory_id])
        return True
            
    def search_memories(self, query: str = ""):
        """Search memories using Vector Semantic matching."""
        if not self.memories_col:
            return []
            
        if not query or len(query.strip()) == 0:
            # Fallback if no query: just return up to 15 recent memories
            data = self.memories_col.get(limit=15)
            # Match the legacy tuple return format for backwards compatibility
            results = []
            if data and data['documents']:
                for i in range(len(data['documents'])):
                    meta = data['metadatas'][i]
                    results.append((meta['category'], meta['key'], data['documents'][i], meta['importance']))
            return results
            
        results_data = self.memories_col.query(
            query_texts=[query],
            n_results=15
        )
        
        # Format results to match the legacy (category, key, value, importance) tuple return
        results = []
        if results_data and results_data['documents'] and len(results_data['documents']) > 0:
            docs = results_data['documents'][0]
            metas = results_data['metadatas'][0]
            for i in range(len(docs)):
                meta = metas[i]
                results.append((meta['category'], meta['key'], docs[i], meta['importance']))
                
        return results
            
    def search_chat_history(self, query: str, limit: int = 3):
        """Search past conversations in chat history using Vector Semantic matching."""
        if not self.chat_history_col:
            return []
            
        results_data = self.chat_history_col.query(
            query_texts=[query],
            n_results=limit
        )
        
        results = []
        if results_data and results_data['documents'] and len(results_data['documents']) > 0:
            docs = results_data['documents'][0]
            metas = results_data['metadatas'][0]
            for i in range(len(docs)):
                meta = metas[i]
                # Match the legacy tuple return format: (role, message, timestamp)
                results.append((meta['role'], docs[i], meta.get('timestamp', '')))
                
        return results
