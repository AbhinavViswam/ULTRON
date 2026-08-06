import sqlite3
import os
from datetime import datetime

class Database:
    def __init__(self, db_path="data/ultron.db"):
        # Ensure the directory exists
        # Make path relative to the project root (where main.py is)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        full_db_path = os.path.join(base_dir, db_path)
        
        os.makedirs(os.path.dirname(full_db_path), exist_ok=True)
        self.db_path = full_db_path
        self._init_db()

    def _init_db(self):
        """Initialize the database tables if they do not exist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create chat_history table
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
            
            # Create tasks table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    description TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    scheduled_for DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create memories table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    importance INTEGER DEFAULT 5,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()

    def save_message(self, session_id: str, role: str, message: str):
        """Save a message to the chat history log with a session ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO chat_history (session_id, role, message)
                VALUES (?, ?, ?)
            ''', (session_id, role, message))
            conn.commit()

    def add_task(self, description: str, scheduled_for: str = None):
        """Add a new task for scheduling."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO tasks (description, scheduled_for)
                VALUES (?, ?)
            ''', (description, scheduled_for))
            conn.commit()

    def get_pending_tasks(self):
        """Retrieve all pending tasks."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, description, scheduled_for, created_at 
                FROM tasks 
                WHERE status = 'pending'
            ''')
            return cursor.fetchall()
            
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
    def save_memory(self, category: str, key: str, value: str, importance: int):
        """Save a new memory to the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO memories (category, key, value, importance)
                VALUES (?, ?, ?, ?)
            ''', (category, key, value, importance))
            conn.commit()
            
    def search_memories(self, query: str = ""):
        """Search memories using smart keyword matching, or return all top memories as fallback."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            if not query or len(query.strip()) == 0:
                cursor.execute('SELECT category, key, value, importance FROM memories ORDER BY importance DESC LIMIT 15')
                return cursor.fetchall()
                
            stop_words = {"who", "what", "where", "is", "my", "am", "i", "the", "a", "an", "user", "identity", "me", "tell", "details"}
            words = [w.strip() for w in query.lower().split() if w.strip() not in stop_words and len(w.strip()) > 1]
            
            if not words:
                cursor.execute('SELECT category, key, value, importance FROM memories ORDER BY importance DESC LIMIT 15')
                return cursor.fetchall()
                
            where_clauses = []
            params = []
            for word in words:
                where_clauses.append("(LOWER(key) LIKE ? OR LOWER(value) LIKE ? OR LOWER(category) LIKE ?)")
                term = f"%{word}%"
                params.extend([term, term, term])
                
            sql = f"SELECT category, key, value, importance FROM memories WHERE {' OR '.join(where_clauses)} ORDER BY importance DESC LIMIT 15"
            cursor.execute(sql, params)
            results = cursor.fetchall()
            
            # Fallback: if no specific match, return top memories so AI has context
            if not results:
                cursor.execute('SELECT category, key, value, importance FROM memories ORDER BY importance DESC LIMIT 15')
                results = cursor.fetchall()
                
            return results
            
    def search_chat_history(self, query: str, limit: int = 3):
        """Search past conversations in chat history using a basic LIKE query."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            search_term = f"%{query}%"
            cursor.execute('''
                SELECT role, message, timestamp
                FROM chat_history
                WHERE message LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (search_term, limit))
            return cursor.fetchall()
