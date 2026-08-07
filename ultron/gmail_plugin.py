import os
import base64
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.compose'
]

def authenticate_gmail():
    """Authenticates the user using credentials.json and returns a Gmail API service instance."""
    creds = None
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    token_path = os.path.join(base_dir, 'token.json')
    creds_path = os.path.join(base_dir, 'credentials.json')

    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                os.remove(token_path)
                return authenticate_gmail()
        else:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(
                    "credentials.json not found. Please download OAuth 2.0 Desktop App credentials "
                    "from Google Cloud Console, rename it to credentials.json, and place it in the project root."
                )
            
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            # This opens the browser for authentication
            creds = flow.run_local_server(port=0)
            
        # Save the credentials for the next run
        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    try:
        service = build('gmail', 'v1', credentials=creds)
        return service
    except Exception as e:
        raise Exception(f"Failed to build Gmail service: {e}")

def read_emails(max_results: int = 5) -> str:
    """Reads the most recent unread emails from the inbox.
    Args:
        max_results: Maximum number of emails to retrieve.
    """
    try:
        service = authenticate_gmail()
        # Call the Gmail API to fetch INBOX, UNREAD
        results = service.users().messages().list(userId='me', labelIds=['INBOX', 'UNREAD'], maxResults=max_results).execute()
        messages = results.get('messages', [])

        if not messages:
            return "You have no new unread emails."

        output = []
        for msg in messages:
            msg_data = service.users().messages().get(userId='me', id=msg['id'], format='metadata', metadataHeaders=['From', 'Subject', 'Date']).execute()
            headers = msg_data.get('payload', {}).get('headers', [])
            snippet = msg_data.get('snippet', '')
            
            subject = "No Subject"
            sender = "Unknown Sender"
            date = "Unknown Date"
            
            for header in headers:
                name = header.get('name')
                value = header.get('value')
                if name == 'Subject':
                    subject = value
                elif name == 'From':
                    sender = value
                elif name == 'Date':
                    date = value
            
            output.append(f"From: {sender}\nSubject: {subject}\nDate: {date}\nSnippet: {snippet}\n---")
            
        return "\n".join(output)
    except Exception as e:
        return f"Failed to read emails: {str(e)}"

def send_email(to: str, subject: str, body: str) -> str:
    """Sends an email using the Gmail API.
    Args:
        to: Email address of the recipient.
        subject: Subject of the email.
        body: Body content of the email.
    """
    try:
        service = authenticate_gmail()
        
        message = EmailMessage()
        message.set_content(body)
        message['To'] = to
        message['From'] = 'me'
        message['Subject'] = subject

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_message = {'raw': encoded_message}

        sent_message = service.users().messages().send(userId="me", body=create_message).execute()
        return f"Successfully sent email to {to}. Message ID: {sent_message.get('id')}"
    except Exception as e:
        return f"Failed to send email: {str(e)}"

def draft_email(to: str, subject: str, body: str) -> str:
    """Drafts an email and saves it to the Drafts folder using the Gmail API.
    Args:
        to: Email address of the recipient.
        subject: Subject of the email.
        body: Body content of the email.
    """
    try:
        service = authenticate_gmail()
        
        message = EmailMessage()
        message.set_content(body)
        message['To'] = to
        message['From'] = 'me'
        message['Subject'] = subject

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_message = {'message': {'raw': encoded_message}}

        draft = service.users().drafts().create(userId="me", body=create_message).execute()
        return f"Successfully saved draft email to {to}. Draft ID: {draft.get('id')}"
    except Exception as e:
        return f"Failed to draft email: {str(e)}"
