import win32com.client

def get_selected_file_in_explorer() -> list:
    """Gets a list of absolute file paths that are currently selected in Windows File Explorer.
    Use this when the user refers to 'the selected file', 'these files', or 'this file'.
    """
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        for window in shell.Windows():
            # Check if it's an Explorer window by looking for Document
            if window.Name in ["File Explorer", "Windows Explorer"]:
                items = window.Document.SelectedItems()
                selected = []
                for i in range(items.Count):
                    selected.append(items.Item(i).Path)
                if selected:
                    return selected
    except Exception as e:
        print(f"Error reading explorer selection: {e}")
    return []
