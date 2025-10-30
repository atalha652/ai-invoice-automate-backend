from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import os
import json
import base64
import email
from email.mime.text import MIMEText
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import re

# Gmail API scopes
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify'
]

class GmailService:
    def __init__(self, user_credentials: Dict = None):
        self.creds = None
        self.service = None
        if user_credentials:
            self.creds = Credentials.from_authorized_user_info(user_credentials, SCOPES)

    def authenticate(self) -> bool:
        """Authenticate with Gmail API using user credentials"""
        if not self.creds:
            return False
        
        try:
            if self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            
            self.service = build('gmail', 'v1', credentials=self.creds)
            return True
        except Exception as e:
            print(f"Authentication failed: {e}")
            return False
    
    def get_purchase_emails(self, max_results: int = 50, page_token: str = None) -> Dict:
        """Fetch purchase emails from Gmail"""
        if not self.service:
            if not self.authenticate():
                raise Exception("Failed to authenticate with Gmail")
        
        try:
            # Query for purchase emails
            query = 'category:purchases'
            
            result = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results,
                pageToken=page_token
            ).execute()
            
            messages = result.get('messages', [])
            next_page_token = result.get('nextPageToken')
            
            # Get detailed information for each message
            detailed_messages = []
            for message in messages:
                try:
                    msg_detail = self.service.users().messages().get(
                        userId='me', 
                        id=message['id'],
                        format='full'
                    ).execute()
                    
                    parsed_email = self._parse_email(msg_detail)
                    if parsed_email:
                        detailed_messages.append(parsed_email)
                        
                except Exception as e:
                    print(f"Error processing message {message['id']}: {e}")
                    continue
            
            return {
                'emails': detailed_messages,
                'next_page_token': next_page_token,
                'total_found': len(detailed_messages)
            }
            
        except HttpError as error:
            print(f'An error occurred: {error}')
            raise Exception(f"Gmail API error: {error}")
    
    def _parse_email(self, message: Dict) -> Optional[Dict]:
        """Parse email message and extract relevant information"""
        try:
            payload = message.get('payload', {})
            headers = payload.get('headers', [])
            
            # Extract headers
            email_data = {
                'id': message['id'],
                'thread_id': message['threadId'],
                'label_ids': message.get('labelIds', []),
                'snippet': message.get('snippet', ''),
                'internal_date': message.get('internalDate'),
                'size_estimate': message.get('sizeEstimate')
            }
            
            # Parse headers
            for header in headers:
                name = header['name'].lower()
                value = header['value']
                
                if name == 'from':
                    email_data['sender'] = value
                    email_data['sender_name'] = self._extract_name_from_email(value)
                    email_data['sender_email'] = self._extract_email_from_string(value)
                elif name == 'to':
                    email_data['recipient'] = value
                elif name == 'subject':
                    email_data['subject'] = value
                elif name == 'date':
                    email_data['date'] = value
                    email_data['parsed_date'] = self._parse_date(value)
            
            # Extract body content
            body_content = self._extract_body(payload)
            email_data['body'] = body_content
            
            # Extract purchase information
            purchase_info = self._extract_purchase_info(email_data)
            email_data.update(purchase_info)
            
            return email_data
            
        except Exception as e:
            print(f"Error parsing email: {e}")
            return None
    
    def _extract_body(self, payload: Dict) -> str:
        """Extract email body content"""
        body = ""
        
        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/plain':
                    data = part['body']['data']
                    body = base64.urlsafe_b64decode(data).decode('utf-8')
                    break
                elif part['mimeType'] == 'text/html':
                    data = part['body']['data']
                    body = base64.urlsafe_b64decode(data).decode('utf-8')
        else:
            if payload['mimeType'] == 'text/plain':
                data = payload['body']['data']
                body = base64.urlsafe_b64decode(data).decode('utf-8')
        
        return body
    
    def _extract_purchase_info(self, email_data: Dict) -> Dict:
        """Extract purchase-related information from email"""
        purchase_info = {
            'amount': None,
            'currency': None,
            'order_number': None,
            'merchant': None,
            'purchase_type': 'unknown'
        }
        
        text_content = f"{email_data.get('subject', '')} {email_data.get('body', '')}"
        
        # Extract amount and currency
        amount_patterns = [
            r'\$(\d+(?:\.\d{2})?)',  # $123.45
            r'(\d+(?:\.\d{2})?)\s*USD',  # 123.45 USD
            r'Total[:\s]*\$?(\d+(?:\.\d{2})?)',  # Total: $123.45
            r'Amount[:\s]*\$?(\d+(?:\.\d{2})?)',  # Amount: $123.45
        ]
        
        for pattern in amount_patterns:
            match = re.search(pattern, text_content, re.IGNORECASE)
            if match:
                purchase_info['amount'] = float(match.group(1))
                purchase_info['currency'] = 'USD'
                break
        
        # Extract order number
        order_patterns = [
            r'Order\s*#?[:\s]*([A-Z0-9\-]+)',
            r'Order\s*Number[:\s]*([A-Z0-9\-]+)',
            r'Transaction\s*ID[:\s]*([A-Z0-9\-]+)',
        ]
        
        for pattern in order_patterns:
            match = re.search(pattern, text_content, re.IGNORECASE)
            if match:
                purchase_info['order_number'] = match.group(1)
                break
        
        # Determine merchant from sender
        sender_name = email_data.get('sender_name', '')
        if sender_name:
            purchase_info['merchant'] = sender_name
        
        # Determine purchase type
        subject = email_data.get('subject', '').lower()
        if any(word in subject for word in ['receipt', 'invoice', 'purchase', 'order']):
            purchase_info['purchase_type'] = 'receipt'
        elif any(word in subject for word in ['shipping', 'shipped', 'delivery']):
            purchase_info['purchase_type'] = 'shipping'
        elif any(word in subject for word in ['refund', 'return']):
            purchase_info['purchase_type'] = 'refund'
        
        return purchase_info
    
    def _extract_name_from_email(self, email_string: str) -> str:
        """Extract name from email string like 'John Doe <john@example.com>'"""
        match = re.match(r'^([^<]+)<', email_string)
        if match:
            return match.group(1).strip().strip('"')
        return email_string.split('@')[0] if '@' in email_string else email_string
    
    def _extract_email_from_string(self, email_string: str) -> str:
        """Extract email address from string"""
        match = re.search(r'<([^>]+)>', email_string)
        if match:
            return match.group(1)
        return email_string if '@' in email_string else ''
    
    def _parse_date(self, date_string: str) -> Optional[str]:
        """Parse date string to ISO format"""
        try:
            # This is a simplified date parser
            # You might want to use a more robust library like dateutil
            return date_string
        except:
            return None
    
    def search_emails(self, query: str, max_results: int = 50) -> Dict:
        """Search emails with custom query"""
        if not self.service:
            if not self.authenticate():
                raise Exception("Failed to authenticate with Gmail")
        
        try:
            result = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()
            
            messages = result.get('messages', [])
            
            detailed_messages = []
            for message in messages:
                try:
                    msg_detail = self.service.users().messages().get(
                        userId='me', 
                        id=message['id'],
                        format='full'
                    ).execute()
                    
                    parsed_email = self._parse_email(msg_detail)
                    if parsed_email:
                        detailed_messages.append(parsed_email)
                        
                except Exception as e:
                    continue
            
            return {
                'emails': detailed_messages,
                'total_found': len(detailed_messages),
                'query': query
            }
            
        except HttpError as error:
            raise Exception(f"Gmail API error: {error}")