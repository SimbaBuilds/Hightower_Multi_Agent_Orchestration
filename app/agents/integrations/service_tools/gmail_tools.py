"""
Gmail API Tools

Provides Gmail functionality for AI agents including:
- Send, read, and manage emails
- Search and filter messages
- Manage labels and threads
- Handle attachments
"""

import json
import base64
from typing import Dict, Any, List, Optional
from uuid import UUID
from app.agents.models import Action
from app.services.token_manager import TokenManager, TokenError
from .base_service import (
    make_authenticated_request,
    create_service_action,
    parse_tool_input,
    AuthenticationError,
    APIError
)

BASE_URL = "https://gmail.googleapis.com/gmail/v1"


def extract_message_body(payload: Dict[str, Any]) -> str:
    """Helper function to extract message body from Gmail payload"""
    body = ""
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    body = base64.urlsafe_b64decode(data).decode("utf-8")
                    break
    else:
        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                body = base64.urlsafe_b64decode(data).decode("utf-8")
    return body


async def send_email(user_id: UUID, params: Dict[str, Any]) -> str:
    """Send an email via Gmail API"""
    try:
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        to = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        cc = params.get("cc", "")
        bcc = params.get("bcc", "")
        
        if not to or not subject:
            return "Error: Both 'to' and 'subject' are required"
            
        # Create email message
        message = f"To: {to}\n"
        if cc:
            message += f"Cc: {cc}\n"
        if bcc:
            message += f"Bcc: {bcc}\n"
        message += f"Subject: {subject}\n\n{body}"
        
        # Encode message
        raw_message = base64.urlsafe_b64encode(message.encode()).decode()
        
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {"raw": raw_message}
        
        response = make_authenticated_request(
            "POST",
            f"{BASE_URL}/users/me/messages/send",
            headers,
            "Gmail",
            data
        )
        
        return f"Email sent successfully. Message ID: {response.get('id', 'Unknown')}"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error sending email: {str(e)}"


async def get_messages(user_id: UUID, params: Dict[str, Any]) -> str:
    """Get list of messages from Gmail"""
    try:
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        query = params.get("query", "")
        max_results = min(int(params.get("max_results", 10)), 50)
        include_content = params.get("include_content", False)
        
        headers = {"Authorization": f"Bearer {access_token}"}
        url_params = {
            "maxResults": str(max_results)
        }
        if query:
            url_params["q"] = query
            
        response = make_authenticated_request(
            "GET",
            f"{BASE_URL}/users/me/messages",
            headers,
            "Gmail",
            params=url_params
        )
        
        messages = response.get("messages", [])
        if not messages:
            return "No messages found"
            
        # Get details for each message
        results = []
        for msg in messages[:max_results]:
            msg_detail = make_authenticated_request(
                "GET",
                f"{BASE_URL}/users/me/messages/{msg['id']}",
                headers,
                "Gmail"
            )
            
            # Extract basic info
            payload = msg_detail.get("payload", {})
            headers_list = payload.get("headers", [])
            
            subject = next((h["value"] for h in headers_list if h["name"].lower() == "subject"), "No Subject")
            sender = next((h["value"] for h in headers_list if h["name"].lower() == "from"), "Unknown Sender")
            date = next((h["value"] for h in headers_list if h["name"].lower() == "date"), "Unknown Date")
            
            # Extract recipient information for better sent email support
            to_recipients = next((h["value"] for h in headers_list if h["name"].lower() == "to"), "")
            cc_recipients = next((h["value"] for h in headers_list if h["name"].lower() == "cc"), "")
            bcc_recipients = next((h["value"] for h in headers_list if h["name"].lower() == "bcc"), "")
            
            msg_info = {
                "id": msg["id"],
                "subject": subject,
                "from": sender,
                "date": date,
                "snippet": msg_detail.get("snippet", "")
            }
            
            # Add full body content if requested
            if include_content:
                body = extract_message_body(payload)
                msg_info["body"] = body
            
            # Add recipient information if present (useful for sent emails)
            if to_recipients:
                msg_info["to"] = to_recipients
            if cc_recipients:
                msg_info["cc"] = cc_recipients
            if bcc_recipients:
                msg_info["bcc"] = bcc_recipients
                
            results.append(msg_info)
            
        return json.dumps(results, indent=2)
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error getting messages: {str(e)}"


async def read_message(user_id: UUID, params: Dict[str, Any]) -> str:
    """Read a specific email message"""
    try:
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        message_id = params.get("message_id", "")
        if not message_id:
            return "Error: message_id is required"
            
        headers = {"Authorization": f"Bearer {access_token}"}
        
        response = make_authenticated_request(
            "GET",
            f"{BASE_URL}/users/me/messages/{message_id}",
            headers,
            "Gmail"
        )
        
        # Extract message details
        payload = response.get("payload", {})
        headers_list = payload.get("headers", [])
        
        result = {}
        for header in headers_list:
            name = header["name"].lower()
            if name in ["subject", "from", "to", "date", "cc", "bcc"]:
                result[name] = header["value"]
        
        # Extract body
        body = ""
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        body = base64.urlsafe_b64decode(data).decode("utf-8")
                        break
        else:
            if payload.get("mimeType") == "text/plain":
                data = payload.get("body", {}).get("data", "")
                if data:
                    body = base64.urlsafe_b64decode(data).decode("utf-8")
        
        result["body"] = body
        result["snippet"] = response.get("snippet", "")
        
        return json.dumps(result, indent=2)
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error reading message: {str(e)}"


async def search_emails(user_id: UUID, params: Dict[str, Any]) -> str:
    """Search emails with advanced Gmail search syntax"""
    try:
        query = params.get("query", "")
        max_results = min(int(params.get("max_results", 10)), 25)
        
        if not query:
            return "Error: search query is required"
            
        return await get_messages(user_id, {"query": query, "max_results": max_results})
        
    except Exception as e:
        return f"Error searching emails: {str(e)}"


async def get_sent_emails_with_recipients(user_id: UUID, params: Dict[str, Any]) -> str:
    """Get recently sent emails with recipient information (optimized for sent email queries)"""
    try:
        max_results = min(int(params.get("max_results", 10)), 25)
        days_back = int(params.get("days_back", 7))  # Look back 7 days by default
        include_content = params.get("include_content", False)
        
        # Use Gmail search syntax to find sent emails from the last N days
        query = f"in:sent newer_than:{days_back}d"
        
        return await get_messages(user_id, {
            "query": query, 
            "max_results": max_results,
            "include_content": include_content
        })
        
    except Exception as e:
        return f"Error getting sent emails: {str(e)}"


async def get_labels(user_id: UUID, params: Dict[str, Any]) -> str:
    """Get list of Gmail labels"""
    try:
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        
        response = make_authenticated_request(
            "GET",
            f"{BASE_URL}/users/me/labels",
            headers,
            "Gmail"
        )
        
        labels = response.get("labels", [])
        result = [{"id": label["id"], "name": label["name"], "type": label.get("type", "user")} for label in labels]
        
        return json.dumps(result, indent=2)
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error getting labels: {str(e)}"


async def create_label(user_id: UUID, params: Dict[str, Any]) -> str:
    """Create a new Gmail label"""
    try:
        name = params.get("name", "")
        if not name:
            return "Error: label name is required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
            
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {
            "name": name,
            "messageListVisibility": "show",
            "labelListVisibility": "labelShow"
        }
        
        response = make_authenticated_request(
            "POST",
            f"{BASE_URL}/users/me/labels",
            headers,
            "Gmail",
            data
        )
        
        return f"Label created successfully: {response.get('name', 'Unknown')} (ID: {response.get('id', 'Unknown')})"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error creating label: {str(e)}"


async def modify_message_labels(user_id: UUID, params: Dict[str, Any]) -> str:
    """Add or remove labels from a message"""
    try:
        message_id = params.get("message_id", "")
        add_labels = params.get("add_labels", [])
        remove_labels = params.get("remove_labels", [])
        
        if not message_id:
            return "Error: message_id is required"
        if not add_labels and not remove_labels:
            return "Error: specify labels to add or remove"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
            
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {}
        if add_labels:
            data["addLabelIds"] = add_labels
        if remove_labels:
            data["removeLabelIds"] = remove_labels
            
        response = make_authenticated_request(
            "POST",
            f"{BASE_URL}/users/me/messages/{message_id}/modify",
            headers,
            "Gmail",
            data
        )
        
        return f"Message labels modified successfully. Message ID: {response.get('id', 'Unknown')}"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error modifying labels: {str(e)}"


async def delete_message(user_id: UUID, params: Dict[str, Any]) -> str:
    """Delete a Gmail message"""
    try:
        message_id = params.get("message_id", "")
        if not message_id:
            return "Error: message_id is required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
            
        headers = {"Authorization": f"Bearer {access_token}"}
        
        make_authenticated_request(
            "DELETE",
            f"{BASE_URL}/users/me/messages/{message_id}",
            headers,
            "Gmail"
        )
        
        return f"Message deleted successfully: {message_id}"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error deleting message: {str(e)}"


async def mark_as_read(user_id: UUID, params: Dict[str, Any]) -> str:
    """Mark message as read"""
    try:
        message_id = params.get("message_id", "")
        if not message_id:
            return "Error: message_id is required"
            
        return await modify_message_labels(user_id, {
            "message_id": message_id,
            "remove_labels": ["UNREAD"]
        })
        
    except Exception as e:
        return f"Error marking as read: {str(e)}"


async def mark_as_unread(user_id: UUID, params: Dict[str, Any]) -> str:
    """Mark a message as unread"""
    try:
        message_id = params.get("message_id", "")
        if not message_id:
            return "Error: message_id is required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
            
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {
            "addLabelIds": ["UNREAD"]
        }
        
        make_authenticated_request(
            "POST",
            f"{BASE_URL}/users/me/messages/{message_id}/modify",
            headers,
            "Gmail",
            data
        )
        
        return f"Message {message_id} marked as unread"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error marking message as unread: {str(e)}"


async def reply_to_message(user_id: UUID, params: Dict[str, Any]) -> str:
    """Reply to an email message"""
    try:
        message_id = params.get("message_id", "")
        body = params.get("body", "")
        
        if not message_id or not body:
            return "Error: Both 'message_id' and 'body' are required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Get original message to extract thread ID and headers
        original = make_authenticated_request(
            "GET",
            f"{BASE_URL}/users/me/messages/{message_id}",
            headers,
            "Gmail"
        )
        
        thread_id = original.get("threadId")
        payload = original.get("payload", {})
        original_headers = payload.get("headers", [])
        
        # Extract necessary headers for reply
        subject = next((h["value"] for h in original_headers if h["name"].lower() == "subject"), "")
        from_addr = next((h["value"] for h in original_headers if h["name"].lower() == "from"), "")
        message_id_header = next((h["value"] for h in original_headers if h["name"].lower() == "message-id"), "")
        
        # Prepare reply subject
        reply_subject = subject if subject.startswith("Re: ") else f"Re: {subject}"
        
        # Create reply message
        reply_message = f"To: {from_addr}\n"
        reply_message += f"Subject: {reply_subject}\n"
        reply_message += f"In-Reply-To: {message_id_header}\n"
        reply_message += f"References: {message_id_header}\n"
        reply_message += f"\n{body}"
        
        # Encode message
        raw_message = base64.urlsafe_b64encode(reply_message.encode()).decode()
        
        data = {
            "raw": raw_message,
            "threadId": thread_id
        }
        
        response = make_authenticated_request(
            "POST",
            f"{BASE_URL}/users/me/messages/send",
            headers,
            "Gmail",
            data
        )
        
        return f"Reply sent successfully. Message ID: {response.get('id', 'Unknown')}"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error sending reply: {str(e)}"


async def forward_message(user_id: UUID, params: Dict[str, Any]) -> str:
    """Forward an email message"""
    try:
        message_id = params.get("message_id", "")
        to = params.get("to", "")
        body = params.get("body", "")
        
        if not message_id or not to:
            return "Error: Both 'message_id' and 'to' are required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Get original message
        original = make_authenticated_request(
            "GET",
            f"{BASE_URL}/users/me/messages/{message_id}",
            headers,
            "Gmail"
        )
        
        payload = original.get("payload", {})
        original_headers = payload.get("headers", [])
        
        # Extract original message details
        subject = next((h["value"] for h in original_headers if h["name"].lower() == "subject"), "")
        from_addr = next((h["value"] for h in original_headers if h["name"].lower() == "from"), "")
        date = next((h["value"] for h in original_headers if h["name"].lower() == "date"), "")
        
        # Extract original body
        original_body = ""
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        original_body = base64.urlsafe_b64decode(data).decode("utf-8")
                        break
        else:
            if payload.get("mimeType") == "text/plain":
                data = payload.get("body", {}).get("data", "")
                if data:
                    original_body = base64.urlsafe_b64decode(data).decode("utf-8")
        
        # Prepare forward subject
        forward_subject = subject if subject.startswith("Fwd: ") else f"Fwd: {subject}"
        
        # Create forward message
        forward_message = f"To: {to}\n"
        forward_message += f"Subject: {forward_subject}\n\n"
        if body:
            forward_message += f"{body}\n\n"
        forward_message += f"---------- Forwarded message ---------\n"
        forward_message += f"From: {from_addr}\n"
        forward_message += f"Date: {date}\n"
        forward_message += f"Subject: {subject}\n\n"
        forward_message += original_body
        
        # Encode message
        raw_message = base64.urlsafe_b64encode(forward_message.encode()).decode()
        
        data = {"raw": raw_message}
        
        response = make_authenticated_request(
            "POST",
            f"{BASE_URL}/users/me/messages/send",
            headers,
            "Gmail",
            data
        )
        
        return f"Message forwarded successfully. Message ID: {response.get('id', 'Unknown')}"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error forwarding message: {str(e)}"


async def create_draft(user_id: UUID, params: Dict[str, Any]) -> str:
    """Create a draft message"""
    try:
        to = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        cc = params.get("cc", "")
        bcc = params.get("bcc", "")
        
        if not to or not subject:
            return "Error: Both 'to' and 'subject' are required"
        
        # Create email message
        message = f"To: {to}\n"
        if cc:
            message += f"Cc: {cc}\n"
        if bcc:
            message += f"Bcc: {bcc}\n"
        message += f"Subject: {subject}\n\n{body}"
        
        # Encode message
        raw_message = base64.urlsafe_b64encode(message.encode()).decode()
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {
            "message": {
                "raw": raw_message
            }
        }
        
        response = make_authenticated_request(
            "POST",
            f"{BASE_URL}/users/me/drafts",
            headers,
            "Gmail",
            data
        )
        
        return f"Draft created successfully. Draft ID: {response.get('id', 'Unknown')}"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error creating draft: {str(e)}"


async def update_draft(user_id: UUID, params: Dict[str, Any]) -> str:
    """Update an existing draft"""
    try:
        draft_id = params.get("draft_id", "")
        to = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        cc = params.get("cc", "")
        bcc = params.get("bcc", "")
        
        if not draft_id:
            return "Error: draft_id is required"
        
        if not to or not subject:
            return "Error: Both 'to' and 'subject' are required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        # Create email message
        message = f"To: {to}\n"
        if cc:
            message += f"Cc: {cc}\n"
        if bcc:
            message += f"Bcc: {bcc}\n"
        message += f"Subject: {subject}\n\n{body}"
        
        # Encode message
        raw_message = base64.urlsafe_b64encode(message.encode()).decode()
        
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {
            "message": {
                "raw": raw_message
            }
        }
        
        response = make_authenticated_request(
            "PUT",
            f"{BASE_URL}/users/me/drafts/{draft_id}",
            headers,
            "Gmail",
            data
        )
        
        return f"Draft updated successfully. Draft ID: {response.get('id', 'Unknown')}"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error updating draft: {str(e)}"


async def send_draft(user_id: UUID, params: Dict[str, Any]) -> str:
    """Send an existing draft"""
    try:
        draft_id = params.get("draft_id", "")
        
        if not draft_id:
            return "Error: draft_id is required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {}
        
        response = make_authenticated_request(
            "POST",
            f"{BASE_URL}/users/me/drafts/{draft_id}/send",
            headers,
            "Gmail",
            data
        )
        
        return f"Draft sent successfully. Message ID: {response.get('id', 'Unknown')}"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error sending draft: {str(e)}"


async def get_thread(user_id: UUID, params: Dict[str, Any]) -> str:
    """Get a conversation thread"""
    try:
        thread_id = params.get("thread_id", "")
        
        if not thread_id:
            return "Error: thread_id is required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        
        response = make_authenticated_request(
            "GET",
            f"{BASE_URL}/users/me/threads/{thread_id}",
            headers,
            "Gmail"
        )
        
        messages = response.get("messages", [])
        results = []
        
        for msg in messages:
            payload = msg.get("payload", {})
            headers_list = payload.get("headers", [])
            
            subject = next((h["value"] for h in headers_list if h["name"].lower() == "subject"), "No Subject")
            sender = next((h["value"] for h in headers_list if h["name"].lower() == "from"), "Unknown Sender")
            date = next((h["value"] for h in headers_list if h["name"].lower() == "date"), "Unknown Date")
            
            results.append({
                "id": msg["id"],
                "subject": subject,
                "from": sender,
                "date": date,
                "snippet": msg.get("snippet", "")
            })
        
        return json.dumps({
            "thread_id": thread_id,
            "messages": results
        }, indent=2)
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error getting thread: {str(e)}"


async def archive_message(user_id: UUID, params: Dict[str, Any]) -> str:
    """Archive a message (remove from INBOX)"""
    try:
        message_id = params.get("message_id", "")
        
        if not message_id:
            return "Error: message_id is required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {
            "removeLabelIds": ["INBOX"]
        }
        
        make_authenticated_request(
            "POST",
            f"{BASE_URL}/users/me/messages/{message_id}/modify",
            headers,
            "Gmail",
            data
        )
        
        return f"Message {message_id} archived successfully"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error archiving message: {str(e)}"


async def unarchive_message(user_id: UUID, params: Dict[str, Any]) -> str:
    """Unarchive a message (add back to INBOX)"""
    try:
        message_id = params.get("message_id", "")
        
        if not message_id:
            return "Error: message_id is required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {
            "addLabelIds": ["INBOX"]
        }
        
        make_authenticated_request(
            "POST",
            f"{BASE_URL}/users/me/messages/{message_id}/modify",
            headers,
            "Gmail",
            data
        )
        
        return f"Message {message_id} unarchived successfully"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error unarchiving message: {str(e)}"


async def star_message(user_id: UUID, params: Dict[str, Any]) -> str:
    """Star a message"""
    try:
        message_id = params.get("message_id", "")
        
        if not message_id:
            return "Error: message_id is required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {
            "addLabelIds": ["STARRED"]
        }
        
        make_authenticated_request(
            "POST",
            f"{BASE_URL}/users/me/messages/{message_id}/modify",
            headers,
            "Gmail",
            data
        )
        
        return f"Message {message_id} starred successfully"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error starring message: {str(e)}"


async def unstar_message(user_id: UUID, params: Dict[str, Any]) -> str:
    """Unstar a message"""
    try:
        message_id = params.get("message_id", "")
        
        if not message_id:
            return "Error: message_id is required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {
            "removeLabelIds": ["STARRED"]
        }
        
        make_authenticated_request(
            "POST",
            f"{BASE_URL}/users/me/messages/{message_id}/modify",
            headers,
            "Gmail",
            data
        )
        
        return f"Message {message_id} unstarred successfully"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error unstarring message: {str(e)}"


async def download_attachment(user_id: UUID, params: Dict[str, Any]) -> str:
    """Download an email attachment"""
    try:
        message_id = params.get("message_id", "")
        attachment_id = params.get("attachment_id", "")
        
        if not message_id or not attachment_id:
            return "Error: Both 'message_id' and 'attachment_id' are required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        
        response = make_authenticated_request(
            "GET",
            f"{BASE_URL}/users/me/messages/{message_id}/attachments/{attachment_id}",
            headers,
            "Gmail"
        )
        
        attachment_data = response.get("data", "")
        size = response.get("size", 0)
        
        # Decode the attachment data
        decoded_data = base64.urlsafe_b64decode(attachment_data)
        
        return f"Attachment downloaded successfully. Size: {size} bytes. Data length: {len(decoded_data)} bytes"
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error downloading attachment: {str(e)}"


async def list_attachments(user_id: UUID, params: Dict[str, Any]) -> str:
    """List attachments in a message"""
    try:
        message_id = params.get("message_id", "")
        
        if not message_id:
            return "Error: message_id is required"
        
        # Get valid access token from database
        token_manager = TokenManager()
        access_token = await token_manager.get_valid_token(user_id, "Gmail")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        
        response = make_authenticated_request(
            "GET",
            f"{BASE_URL}/users/me/messages/{message_id}",
            headers,
            "Gmail"
        )
        
        attachments = []
        
        def extract_attachments(part):
            if part.get("filename") and part.get("body", {}).get("attachmentId"):
                attachments.append({
                    "filename": part["filename"],
                    "attachment_id": part["body"]["attachmentId"],
                    "mime_type": part.get("mimeType", ""),
                    "size": part["body"].get("size", 0)
                })
            
            if "parts" in part:
                for subpart in part["parts"]:
                    extract_attachments(subpart)
        
        payload = response.get("payload", {})
        extract_attachments(payload)
        
        if not attachments:
            return "No attachments found in this message"
        
        return json.dumps(attachments, indent=2)
        
    except TokenError as e:
        return f"Token Error: {str(e)}"
    except (AuthenticationError, APIError) as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error listing attachments: {str(e)}"


# Action handlers with injected user_id
async def send_email_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await send_email(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def get_messages_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await get_messages(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def read_message_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await read_message(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def search_emails_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await search_emails(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def get_sent_emails_with_recipients_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await get_sent_emails_with_recipients(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def get_labels_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await get_labels(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def create_label_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await create_label(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def modify_message_labels_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await modify_message_labels(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def delete_message_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await delete_message(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def mark_as_read_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await mark_as_read(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def mark_as_unread_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await mark_as_unread(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def reply_to_message_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await reply_to_message(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def forward_message_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await forward_message(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def create_draft_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await create_draft(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def update_draft_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await update_draft(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def send_draft_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await send_draft(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def get_thread_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await get_thread(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def archive_message_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await archive_message(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def unarchive_message_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await unarchive_message(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def star_message_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await star_message(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def unstar_message_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await unstar_message(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def download_attachment_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await download_attachment(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


async def list_attachments_handler(input_str: str, user_id: str) -> str:
    if not user_id:
        return "Error: user_id is required"
    params = parse_tool_input(input_str)
    try:
        user_uuid = UUID(user_id)
        return await list_attachments(user_uuid, params)
    except ValueError:
        return "Error: Invalid user_id format"


# Factory functions for Gmail actions with injected user_id
def create_gmail_send_email_action(user_id: str = None) -> Action:
    """Create send email action with injected user_id"""
    return create_service_action(
        name="gmail_send_email",
        description="Send an email via Gmail",
        parameters={
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string", "description": "Email subject"},
            "body": {"type": "string", "description": "Email body content"},
            "cc": {"type": "string", "description": "CC recipients (optional)"},
            "bcc": {"type": "string", "description": "BCC recipients (optional)"}
        },
        returns="Confirmation message with sent email ID",
        handler_func=lambda input_str: send_email_handler(input_str, user_id),
        example='Action: gmail_send_email: "{"to": "user@example.com", "subject": "Hello", "body": "Test message"}"'
    )

def create_gmail_get_messages_action(user_id: str = None) -> Action:
    """Create get messages action with injected user_id"""
    return create_service_action(
        name="gmail_get_messages",
        description="Get list of recent Gmail messages",
        parameters={
            "query": {"type": "string", "description": "Gmail search query (optional)"},
            "max_results": {"type": "integer", "description": "Maximum number of messages to return (1-50, default 10)"},
            "include_content": {"type": "boolean", "description": "Include full email body content (default false)"}
        },
        returns="JSON list of messages with subject, sender, date, snippet, recipient information (to, cc, bcc when present), and optionally full body content",
        handler_func=lambda input_str: get_messages_handler(input_str, user_id),
        example='Action: gmail_get_messages: "{"query": "is:unread", "max_results": 5, "include_content": true}"'
    )

def create_gmail_read_message_action(user_id: str = None) -> Action:
    """Create read message action with injected user_id"""
    return create_service_action(
        name="gmail_read_message",
        description="Read full content of a specific Gmail message",
        parameters={
            "message_id": {"type": "string", "description": "Gmail message ID"}
        },
        returns="JSON object with full message details including headers and body",
        handler_func=lambda input_str: read_message_handler(input_str, user_id),
        example='Action: gmail_read_message: "{"message_id": "abc123def456"}"'
    )

def create_gmail_search_emails_action(user_id: str = None) -> Action:
    """Create search emails action with injected user_id"""
    return create_service_action(
        name="gmail_search_emails",
        description="Search Gmail messages using Gmail search syntax",
        parameters={
            "query": {"type": "string", "description": "Gmail search query (e.g., 'from:user@example.com', 'has:attachment', 'subject:important', 'in:sent')"},
            "max_results": {"type": "integer", "description": "Maximum number of results (1-50, default 10)"}
        },
        returns="JSON list of matching messages with subject, sender, date, snippet, and recipient information (to, cc, bcc when present)",
        handler_func=lambda input_str: search_emails_handler(input_str, user_id),
        example='Action: gmail_search_emails: "{"query": "from:boss@company.com has:attachment", "max_results": 10}"'
    )

def create_gmail_get_sent_emails_with_recipients_action(user_id: str = None) -> Action:
    """Create get sent emails action with injected user_id"""
    return create_service_action(
        name="gmail_get_sent_emails_with_recipients",
        description="Get recently sent emails with recipient information and optionally full content (contact/contacts)",
        parameters={
            "max_results": {"type": "integer", "description": "Maximum number of results (1-50, default 10)"},
            "days_back": {"type": "integer", "description": "Number of days to look back for sent emails (default 7)"},
            "include_content": {"type": "boolean", "description": "Include full email body content (default false)"}
        },
        returns="JSON list of sent emails with recipient information and optionally full body content",
        handler_func=lambda input_str: get_sent_emails_with_recipients_handler(input_str, user_id),
        example='Action: gmail_get_sent_emails_with_recipients: "{"max_results": 10, "days_back": 7, "include_content": true}"'
    )

def create_gmail_get_labels_action(user_id: str = None) -> Action:
    """Create get labels action with injected user_id"""
    return create_service_action(
        name="gmail_get_labels",
        description="Get list of Gmail labels",
        parameters={},
        returns="JSON list of labels with IDs and names",
        handler_func=lambda input_str: get_labels_handler(input_str, user_id),
        example='Action: gmail_get_labels: "{}"'
    )

def create_gmail_create_label_action(user_id: str = None) -> Action:
    """Create create label action with injected user_id"""
    return create_service_action(
        name="gmail_create_label",
        description="Create a new Gmail label",
        parameters={
            "name": {"type": "string", "description": "Label name"}
        },
        returns="Confirmation message with created label details",
        handler_func=lambda input_str: create_label_handler(input_str, user_id),
        example='Action: gmail_create_label: "{"name": "Project Alpha"}"'
    )

def create_gmail_modify_labels_action(user_id: str = None) -> Action:
    """Create modify labels action with injected user_id"""
    return create_service_action(
        name="gmail_modify_labels",
        description="Add or remove labels from a Gmail message",
        parameters={
            "message_id": {"type": "string", "description": "Gmail message ID"},
            "add_labels": {"type": "array", "items": {"type": "string"}, "description": "Label IDs to add"},
            "remove_labels": {"type": "array", "items": {"type": "string"}, "description": "Label IDs to remove"}
        },
        returns="Confirmation message",
        handler_func=lambda input_str: modify_message_labels_handler(input_str, user_id),
        example='Action: gmail_modify_labels: "{"message_id": "abc123", "add_labels": ["IMPORTANT"], "remove_labels": ["UNREAD"]}"'
    )

def create_gmail_delete_message_action(user_id: str = None) -> Action:
    """Create delete message action with injected user_id"""
    return create_service_action(
        name="gmail_delete_message",
        description="Delete a Gmail message permanently",
        parameters={
            "message_id": {"type": "string", "description": "Gmail message ID to delete"}
        },
        returns="Confirmation message",
        handler_func=lambda input_str: delete_message_handler(input_str, user_id),
        example='Action: gmail_delete_message: "{"message_id": "abc123def456"}"'
    )

def create_gmail_mark_read_action(user_id: str = None) -> Action:
    """Create mark as read action with injected user_id"""
    return create_service_action(
        name="gmail_mark_read",
        description="Mark a Gmail message as read",
        parameters={
            "message_id": {"type": "string", "description": "Gmail message ID"}
        },
        returns="Confirmation message",
        handler_func=lambda input_str: mark_as_read_handler(input_str, user_id),
        example='Action: gmail_mark_read: "{"message_id": "abc123def456"}"'
    )

def create_gmail_mark_unread_action(user_id: str = None) -> Action:
    """Create mark as unread action with injected user_id"""
    return create_service_action(
        name="gmail_mark_unread",
        description="Mark a Gmail message as unread",
        parameters={
            "message_id": {"type": "string", "description": "Gmail message ID"}
        },
        returns="Confirmation message",
        handler_func=lambda input_str: mark_as_unread_handler(input_str, user_id),
        example='Action: gmail_mark_unread: "{"message_id": "abc123def456"}"'
    )

def create_gmail_reply_message_action(user_id: str = None) -> Action:
    """Create reply message action with injected user_id"""
    return create_service_action(
        name="gmail_reply_message",
        description="Reply to an email message",
        parameters={        "message_id": {"type": "string", "description": "ID of the message to reply to"},
        "body": {"type": "string", "description": "Reply message body"}
        },
        returns="Confirmation message with sent reply ID",
        handler_func=lambda input_str: reply_to_message_handler(input_str, user_id),
        example='Action: gmail_reply_message: "{"message_id": "abc123", "body": "Thank you for your message."}"'
    )

def create_gmail_forward_message_action(user_id: str = None) -> Action:
    """Create forward message action with injected user_id"""
    return create_service_action(
        name="gmail_forward_message",
        description="Forward an email message",
        parameters={        "message_id": {"type": "string", "description": "ID of the message to forward"},
        "to": {"type": "string", "description": "Recipient email address"},
        "body": {"type": "string", "description": "Optional message to add before forwarded content"}
        },
        returns="Confirmation message with forwarded message ID",
        handler_func=lambda input_str: forward_message_handler(input_str, user_id),
        example='Action: gmail_forward_message: "{"message_id": "abc123", "to": "user@example.com", "body": "Please see below."}"'
    )

def create_gmail_create_draft_action(user_id: str = None) -> Action:
    """Create create draft action with injected user_id"""
    return create_service_action(
        name="gmail_create_draft",
        description="Create a draft email",
        parameters={        "to": {"type": "string", "description": "Recipient email address"},
        "subject": {"type": "string", "description": "Email subject"},
        "body": {"type": "string", "description": "Email body content"},
        "cc": {"type": "string", "description": "CC recipients (optional)"},
        "bcc": {"type": "string", "description": "BCC recipients (optional)"}
        },
        returns="Confirmation message with draft ID",
        handler_func=lambda input_str: create_draft_handler(input_str, user_id),
        example='Action: gmail_create_draft: "{"to": "user@example.com", "subject": "Draft Subject", "body": "Draft content"}"'
    )

def create_gmail_update_draft_action(user_id: str = None) -> Action:
    """Create update draft action with injected user_id"""
    return create_service_action(
        name="gmail_update_draft",
        description="Update an existing draft email",
        parameters={        "draft_id": {"type": "string", "description": "ID of the draft to update"},
        "to": {"type": "string", "description": "Recipient email address"},
        "subject": {"type": "string", "description": "Email subject"},
        "body": {"type": "string", "description": "Email body content"},
        "cc": {"type": "string", "description": "CC recipients (optional)"},
        "bcc": {"type": "string", "description": "BCC recipients (optional)"}
        },
        returns="Confirmation message with updated draft ID",
        handler_func=lambda input_str: update_draft_handler(input_str, user_id),
        example='Action: gmail_update_draft: "{"draft_id": "draft123", "to": "user@example.com", "subject": "Updated Subject", "body": "Updated content"}"'
    )

def create_gmail_send_draft_action(user_id: str = None) -> Action:
    """Create send draft action with injected user_id"""
    return create_service_action(
        name="gmail_send_draft",
        description="Send an existing Gmail draft email. Requires a valid draft_id obtained from gmail_create_draft or gmail_get_messages with 'in:drafts' query.",
        parameters={"draft_id": {"type": "string", "description": "Gmail draft ID (typically 16-character alphanumeric string, e.g., '19855caa46be0302'). Must be from an existing draft that hasn't been sent or deleted."}},
        returns="Confirmation message with sent message ID, or error message if draft_id is invalid/not found",
        handler_func=lambda input_str: send_draft_handler(input_str, user_id),
        example='Action: gmail_send_draft: "{"draft_id": "19855caa46be0302"}"'
    )

def create_gmail_get_thread_action(user_id: str = None) -> Action:
    """Create get thread action with injected user_id"""
    return create_service_action(
        name="gmail_get_thread",
        description="Get a conversation thread with all messages",
        parameters={        "thread_id": {"type": "string", "description": "ID of the thread to retrieve"}
        },
        returns="JSON object with thread details and all messages",
        handler_func=lambda input_str: get_thread_handler(input_str, user_id),
        example='Action: gmail_get_thread: "{"thread_id": "thread123"}"'
    )

def create_gmail_archive_message_action(user_id: str = None) -> Action:
    """Create archive message action with injected user_id"""
    return create_service_action(
        name="gmail_archive_message",
        description="Archive a message (remove from INBOX)",
        parameters={        "message_id": {"type": "string", "description": "ID of the message to archive"}
        },
        returns="Confirmation message",
        handler_func=lambda input_str: archive_message_handler(input_str, user_id),
        example='Action: gmail_archive_message: "{"message_id": "msg123"}"'
    )

def create_gmail_unarchive_message_action(user_id: str = None) -> Action:
    """Create unarchive message action with injected user_id"""
    return create_service_action(
        name="gmail_unarchive_message",
        description="Unarchive a message (add back to INBOX)",
        parameters={        "message_id": {"type": "string", "description": "ID of the message to unarchive"}
        },
        returns="Confirmation message",
        handler_func=lambda input_str: unarchive_message_handler(input_str, user_id),
        example='Action: gmail_unarchive_message: "{"message_id": "msg123"}"'
    )

def create_gmail_star_message_action(user_id: str = None) -> Action:
    """Create star message action with injected user_id"""
    return create_service_action(
        name="gmail_star_message",
        description="Star a message for easy identification",
        parameters={        "message_id": {"type": "string", "description": "ID of the message to star"}
        },
        returns="Confirmation message",
        handler_func=lambda input_str: star_message_handler(input_str, user_id),
        example='Action: gmail_star_message: "{"message_id": "msg123"}"'
    )

def create_gmail_unstar_message_action(user_id: str = None) -> Action:
    """Create unstar message action with injected user_id"""
    return create_service_action(
        name="gmail_unstar_message",
        description="Remove star from a message",
        parameters={        "message_id": {"type": "string", "description": "ID of the message to unstar"}
        },
        returns="Confirmation message",
        handler_func=lambda input_str: unstar_message_handler(input_str, user_id),
        example='Action: gmail_unstar_message: "{"message_id": "msg123"}"'
    )

def create_gmail_download_attachment_action(user_id: str = None) -> Action:
    """Create download attachment action with injected user_id"""
    return create_service_action(
        name="gmail_download_attachment",
        description="Download an email attachment",
        parameters={        "message_id": {"type": "string", "description": "ID of the message containing the attachment"},
        "attachment_id": {"type": "string", "description": "ID of the attachment to download"}
        },
        returns="Confirmation message with attachment details",
        handler_func=lambda input_str: download_attachment_handler(input_str, user_id),
        example='Action: gmail_download_attachment: "{"message_id": "msg123", "attachment_id": "att123"}"'
    )

def create_gmail_list_attachments_action(user_id: str = None) -> Action:
    """Create list attachments action with injected user_id"""
    return create_service_action(
        name="gmail_list_attachments",
        description="List all attachments in a message",
        parameters={        "message_id": {"type": "string", "description": "ID of the message to list attachments for"}
        },
        returns="JSON list of attachments with filenames, IDs, and sizes",
        handler_func=lambda input_str: list_attachments_handler(input_str, user_id),
        example='Action: gmail_list_attachments: "{"message_id": "msg123"}"'
    )

def get_gmail_tools(user_id: str = None) -> List[Action]:
    """Get all Gmail tools with injected user_id"""
    return [
        create_gmail_send_email_action(user_id),
        create_gmail_get_messages_action(user_id),
        create_gmail_read_message_action(user_id),
        create_gmail_search_emails_action(user_id),
        create_gmail_get_sent_emails_with_recipients_action(user_id),
        create_gmail_get_labels_action(user_id),
        create_gmail_create_label_action(user_id),
        create_gmail_modify_labels_action(user_id),
        create_gmail_delete_message_action(user_id),
        create_gmail_mark_read_action(user_id),
        create_gmail_mark_unread_action(user_id),
        # Additional tools
        create_gmail_reply_message_action(user_id),
        create_gmail_forward_message_action(user_id),
        create_gmail_create_draft_action(user_id),
        create_gmail_update_draft_action(user_id),
        create_gmail_send_draft_action(user_id),
        create_gmail_get_thread_action(user_id),
        create_gmail_archive_message_action(user_id),
        create_gmail_unarchive_message_action(user_id),
        create_gmail_star_message_action(user_id),
        create_gmail_unstar_message_action(user_id),
        create_gmail_download_attachment_action(user_id),
        create_gmail_list_attachments_action(user_id)
    ]