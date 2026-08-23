import win32com.client

def search_windows(query: str):
    conn = win32com.client.Dispatch("ADODB.Connection")
    conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
    
    # Query syntax: https://learn.microsoft.com/en-us/windows/win32/search/-search-sql-windowssearch-dialects
    sql = f"SELECT System.ItemPathDisplay FROM SystemIndex WHERE System.FileName LIKE '%{query}%'"
    
    try:
        rs, _ = conn.Execute(sql)
        results = []
        while not rs.EOF:
            results.append(rs.Fields.Item("System.ItemPathDisplay").Value)
            rs.MoveNext()
            if len(results) > 20:
                break
        return results
    except Exception as e:
        return str(e)
    finally:
        conn.Close()

if __name__ == '__main__':
    print(search_windows("ultron"))
