import os

def read_document(file_path: str, page: int = None) -> str:
    """Reads a PDF or DOCX file. If the document is long, you MUST provide a page number (1-indexed) to read it piece by piece."""
    if not os.path.exists(file_path):
        return f"Error: File '{file_path}' not found."

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".pdf":
            return _read_pdf(file_path, page)
        elif ext == ".docx":
            return _read_docx(file_path, page)
        else:
            return f"Error: Unsupported file format '{ext}'. Only .pdf and .docx are supported."
    except Exception as e:
        return f"Error reading document: {e}"

def _read_pdf(file_path: str, page: int = None) -> str:
    try:
        import pypdf
    except ImportError:
        return "Error: pypdf library is not installed. Please run: pip install pypdf"

    with open(file_path, 'rb') as f:
        reader = pypdf.PdfReader(f)
        num_pages = len(reader.pages)
        
        if num_pages == 0:
            return "Document is empty."

        if page is not None:
            if page < 1 or page > num_pages:
                return f"Error: Invalid page number. The document has {num_pages} pages."
            
            page_obj = reader.pages[page - 1]
            text = page_obj.extract_text()
            return f"--- Page {page} of {num_pages} ---\n\n{text}"
            
        else:
            # If no page provided, and document is short, read everything.
            if num_pages <= 3:
                full_text = []
                for i in range(num_pages):
                    text = reader.pages[i].extract_text()
                    full_text.append(f"--- Page {i + 1} ---\n{text}")
                return "\n\n".join(full_text)
            else:
                # Document is too long, return pagination summary
                return (f"The PDF document '{os.path.basename(file_path)}' is long ({num_pages} pages). "
                        f"To read it, please call the tool again and provide a specific page number, e.g., page=1.")

def _read_docx(file_path: str, page: int = None) -> str:
    try:
        import docx
    except ImportError:
        return "Error: python-docx library is not installed. Please run: pip install python-docx"

    doc = docx.Document(file_path)
    full_text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
    
    if not full_text.strip():
        return "Document is empty."

    # Chunk the text into roughly 4000 character segments
    chunk_size = 4000
    chunks = [full_text[i:i+chunk_size] for i in range(0, len(full_text), chunk_size)]
    num_chunks = len(chunks)

    if page is not None:
        if page < 1 or page > num_chunks:
            return f"Error: Invalid page number. The document has {num_chunks} pages/chunks."
        
        text = chunks[page - 1]
        return f"--- Page/Chunk {page} of {num_chunks} ---\n\n{text}"
        
    else:
        # If no page provided, and document is short, read everything.
        if num_chunks <= 3:
            return full_text
        else:
            # Document is too long, return pagination summary
            return (f"The DOCX document '{os.path.basename(file_path)}' is long (divided into {num_chunks} chunks). "
                    f"To read it, please call the tool again and provide a specific page number, e.g., page=1.")
