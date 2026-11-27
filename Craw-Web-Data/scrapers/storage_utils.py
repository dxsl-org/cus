"""
Unified Storage Manager for SQLite output
"""
import sqlite3
import json
import os
import threading

class StorageManager:
    _instances = {}
    _lock = threading.Lock()

    def __new__(cls, db_path):
        with cls._lock:
            if db_path not in cls._instances:
                cls._instances[db_path] = super(StorageManager, cls).__new__(cls)
                cls._instances[db_path]._initialized = False
            return cls._instances[db_path]

    def __init__(self, db_path):
        if self._initialized: return
        self.db_path = db_path
        self._init_db()
        self._initialized = True

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('PRAGMA synchronous = OFF')
        cursor.execute('PRAGMA journal_mode = WAL')
        cursor.execute('PRAGMA cache_size = 10000')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE,
                title TEXT,
                author TEXT,
                content TEXT,
                tags TEXT,
                category TEXT,
                comments TEXT,
                word_count INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT
            )
        ''')
        conn.commit()
        conn.close()

    def insert_articles(self, articles_list):
        """
        Inserts multiple articles in a single transaction.
        articles_list: list of dicts with keys matching insert_article arguments.
        """
        if not articles_list:
            return 0
            
        conn = sqlite3.connect(self.db_path)
        # Apply performance settings per connection
        conn.execute('PRAGMA synchronous = OFF')
        conn.execute('PRAGMA journal_mode = WAL')
        
        cursor = conn.cursor()
        
        data_to_insert = []
        for art in articles_list:
            u = art.get('url')
            t = art.get('title')
            a = art.get('author')
            c = art.get('content')
            tg = art.get('tags')
            cat = art.get('category')
            cm = art.get('comments')
            mc = art.get('word_count')
            meta = art.get('metadata')
            
            tg_j = json.dumps(tg, ensure_ascii=False) if tg is not None else None
            cm_j = json.dumps(cm, ensure_ascii=False) if cm is not None else None
            meta_j = json.dumps(meta, ensure_ascii=False) if meta is not None else None
            
            if mc is None and c:
                mc = len(c.split())
                
            data_to_insert.append((u, t, a, c, tg_j, cat, cm_j, mc, meta_j))
            
        try:
            cursor.executemany('''
                INSERT OR IGNORE INTO articles 
                (url, title, author, content, tags, category, comments, word_count, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', data_to_insert)
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            print(f"Error batch inserting into {self.db_path}: {e}")
            return 0
        finally:
            conn.close()

    def insert_article(self, url, title=None, author=None, content=None, tags=None, category=None, comments=None, word_count=None, metadata=None):
        """
        Inserts a single article. Note: Opening/closing connection per call is slow.
        Use insert_articles for better performance.
        """
        return self.insert_articles([{
            'url': url, 'title': title, 'author': author, 'content': content,
            'tags': tags, 'category': category, 'comments': comments,
            'word_count': word_count, 'metadata': metadata
        }]) > 0

# Default to the repo's dataset/ (where the existing DBs live); the old "D:\Data\dataset"
# no longer exists, so new crawls silently started fresh DBs there.
DEFAULT_DATASET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dataset")

def get_storage(source_id, base_dir=DEFAULT_DATASET_DIR):
    db_path = os.path.join(base_dir, f"{source_id}.db")
    return StorageManager(db_path)
