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
            
    def save_memory(self, category: str, key: str, value: str, importance: int):
        """Save a new memory to the Vector DB."""
        if not self.memories_col:
            return
            
        doc_id = str(uuid.uuid4())
        # Combine into a single semantic string
        semantic_text = f"[{category}] {key}: {value}"
        self.memories_col.add(
            documents=[semantic_text],
            metadatas=[{"category": category, "key": key, "importance": importance}],
            ids=[doc_id]
        )
            
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
