"""Incremental analysis caching for code intelligence tools."""

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, Any, Optional
import os


class AnalysisCache:
    def __init__(self, cache_db_path: str = None):
        if cache_db_path is None:
            cache_dir = Path.home() / ".forge" / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_db_path = str(cache_dir / "analysis_cache.db")
        
        self.cache_db_path = cache_db_path
        self._init_db()
    
    def _init_db(self):
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS analysis_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                tool_version TEXT,
                tool_config_hash TEXT,
                result_json TEXT NOT NULL,
                timestamp REAL NOT NULL,
                UNIQUE(tool_name, file_path, file_hash, tool_config_hash)
            )
        ''')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tool_file ON analysis_cache (tool_name, file_path)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON analysis_cache (timestamp)')
        
        conn.commit()
        conn.close()
    
    def _calculate_file_hash(self, file_path: str) -> str:
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    
    def _calculate_config_hash(self, config: Dict[str, Any]) -> str:
        config_str = json.dumps(config, sort_keys=True)
        return hashlib.sha256(config_str.encode()).hexdigest()
    
    def get_cached_result(self, tool_name: str, file_path: str, 
                         config: Dict[str, Any] = None) -> Optional[Any]:
        if not os.path.exists(file_path):
            return None
            
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        
        try:
            file_hash = self._calculate_file_hash(file_path)
            config_hash = self._calculate_config_hash(config or {})
            
            cursor.execute('''
                SELECT result_json, timestamp
                FROM analysis_cache
                WHERE tool_name = ? AND file_path = ? AND file_hash = ? 
                AND tool_config_hash = ?
            ''', (tool_name, file_path, file_hash, config_hash))
            
            row = cursor.fetchone()
            if row:
                result_json, timestamp = row
                if time.time() - timestamp < 24 * 3600:
                    return json.loads(result_json)
            
            return None
        finally:
            conn.close()
    
    def cache_result(self, tool_name: str, file_path: str, result: Any,
                     config: Dict[str, Any] = None) -> bool:
        if not os.path.exists(file_path):
            return False
            
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        
        try:
            file_hash = self._calculate_file_hash(file_path)
            config_hash = self._calculate_config_hash(config or {})
            result_json = json.dumps(result, default=str)
            timestamp = time.time()
            
            cursor.execute('''
                INSERT OR REPLACE INTO analysis_cache 
                (tool_name, file_path, file_hash, tool_version, tool_config_hash, 
                 result_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (tool_name, file_path, file_hash, "1.0", config_hash, 
                  result_json, timestamp))
            
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()
    
    def invalidate_file_cache(self, file_path: str) -> int:
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('DELETE FROM analysis_cache WHERE file_path = ?', (file_path,))
            deleted_count = cursor.rowcount
            conn.commit()
            return deleted_count
        finally:
            conn.close()
    
    def clear_all_cache(self) -> int:
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('DELETE FROM analysis_cache')
            deleted_count = cursor.rowcount
            conn.commit()
            return deleted_count
        finally:
            conn.close()
    
    def get_cache_stats(self) -> Dict[str, Any]:
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('SELECT COUNT(*) FROM analysis_cache')
            total_entries = cursor.fetchone()[0]
            
            cursor.execute('SELECT tool_name, COUNT(*) FROM analysis_cache GROUP BY tool_name')
            entries_by_tool = dict(cursor.fetchall())
            
            return {
                "total_entries": total_entries,
                "entries_by_tool": entries_by_tool,
            }
        finally:
            conn.close()
