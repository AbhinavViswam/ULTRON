"""search_plugin.py — System-Wide Search and File Locator

Provides tools to search the entire local machine for files or folders,
falling back to a fast drive scan if the Windows Search Index misses it.
"""

import os
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor

def _search_windows_index(query: str) -> list[str]:
    """Searches using the built-in Windows ADO Search index."""
    try:
        import win32com.client
        conn = win32com.client.Dispatch("ADODB.Connection")
        conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        
        # Clean query to avoid SQL injection syntax errors
        clean_query = query.replace("'", "''")
        sql = f"SELECT System.ItemPathDisplay FROM SystemIndex WHERE System.FileName LIKE '%{clean_query}%'"
        
        rs, _ = conn.Execute(sql)
        results = []
        while not rs.EOF:
            results.append(rs.Fields.Item("System.ItemPathDisplay").Value)
            rs.MoveNext()
            if len(results) >= 20:
                break
        conn.Close()
        return results
    except Exception as e:
        print(f"[SearchPlugin] Windows Search Index failed: {e}")
        return []

def _fast_fallback_search(query: str, search_root: str = "C:\\") -> list[str]:
    """Rapidly scans the drive using os.scandir, skipping system folders."""
    query = query.lower()
    results = []
    
    # Common massive/slow directories to skip for speed
    SKIP_DIRS = {
        "windows", "program files", "program files (x86)",
        "programdata", "appdata", "$recycle.bin", "system volume information",
        ".git", "node_modules", ".venv", "venv"
    }

    def scan_dir(path):
        if len(results) >= 10:
            return
        
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if len(results) >= 10:
                        break
                        
                    if query in entry.name.lower():
                        results.append(entry.path)
                        
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.lower() not in SKIP_DIRS:
                            scan_dir(entry.path)
        except (PermissionError, FileNotFoundError, OSError):
            pass

    print(f"[SearchPlugin] Starting fallback scan from {search_root}...")
    scan_dir(search_root)
    return results

def search_and_open(query: str) -> str:
    """Searches the computer for a file or folder and opens its location in File Explorer.
    
    Use this when the user asks to "find", "search for", or "open the location of"
    a specific file or folder anywhere on the computer.
    
    Args:
        query: The name (or partial name) of the file or folder to find.
    """
    if not query.strip():
        return "Please provide a valid file or folder name to search for."
        
    print(f"[SearchPlugin] Searching for '{query}'...")
    start_time = time.time()
    
    # 1. Try fast Windows Search Index first
    matches = _search_windows_index(query)
    
    # 2. If nothing found, do a manual fast scan of C:\
    if not matches:
        print(f"[SearchPlugin] Not found in index. Falling back to drive scan...")
        matches = _fast_fallback_search(query)
        
    elapsed = time.time() - start_time
    
    if not matches:
        return f"Could not find any file or folder matching '{query}' (search took {elapsed:.1f}s)."
        
    # We take the first best match
    best_match = matches[0]
    
    try:
        # Open Explorer and highlight the file/folder
        # Using subprocess to execute the explorer /select command safely
        subprocess.Popen(f'explorer.exe /select,"{best_match}"')
        
        return (f"Found '{os.path.basename(best_match)}' in {elapsed:.1f} seconds.\n"
                f"Opened File Explorer highlighting:\n{best_match}")
    except Exception as e:
        return f"Found the file at '{best_match}', but failed to open Explorer: {e}"
