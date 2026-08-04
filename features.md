# Ultron Desktop Assistant - Features

Welcome to the feature documentation for **Ultron**, your advanced, highly intelligent desktop assistant. Below is an overview of the currently available and planned features for the project.

## 🧠 Core Features

### 1. Intelligent AI Brain
- **Powered by Gemini API:** Utilizes Google's state-of-the-art Generative AI models.
- **Smart Auto-Selection:** Automatically scans available models associated with your API key and selects the best preferred model (e.g., `gemini-3.5-flash`) to ensure optimal speed and intelligence.
- **Custom Persona:** Ultron is programmed with a dedicated system instruction to provide concise, accurate, and professional answers, bringing the assistant persona to life.

### 2. Local Database Logging
- **SQLite Integration:** A lightweight, completely local database (`data/ultron.db`) handles all data storage, ensuring your data never leaves your machine.
- **Session Tracking:** Each time you launch Ultron, a unique Session ID is generated. 
- **Chat History Archive:** All user inputs and Ultron's responses are automatically and silently logged to the database under the current session for future reference and analytics.

### 3. Environment & Setup
- **Strict Version Control:** Locked to Python 3.14 via `.python-version` to ensure environment consistency across different machines.
- **Isolated Dependencies:** Runs entirely within its own virtual environment (`venv`) avoiding conflicts with system packages.
- **Secure Configuration:** Uses `.env` for secure management of sensitive keys like the `GEMINI_API_KEY`.

---

## 🚀 Upcoming / Planned Features

- **Task Management & Scheduling:** The database architecture is already set up with a `tasks` table. Future updates will allow you to assign tasks to Ultron and have them scheduled and tracked.
- **Context-Aware Memory:** While chat history is currently archived, future updates will allow Ultron to automatically load previous context to remember past conversations in active memory.
