# Field Notes

A personal knowledge base and note-taking app built with Python. No external dependencies — runs entirely on Python built-ins.

## Features

- Create, edit, and delete notes with titles and free-form content
- Organize notes into **categories** (personal, professional, other) with custom colors
- Tag notes and filter by tag
- Pin important notes to the top
- Full-text search across title and content
- Sort by newest, oldest, last updated, alphabetical, or category
- Export all notes and categories to JSON
- Persistent local storage via SQLite

## Requirements

- Python 3.x (standard library only — `tkinter`, `sqlite3`, `json`, `pathlib`)

## Usage

```bash
python app.py
```

The app creates a `notes.db` SQLite database in the same directory on first run. Custom categories can be added or removed from within the app.

## Data

All data is stored locally in `notes.db`. Use the built-in JSON export to back up or migrate your notes.
