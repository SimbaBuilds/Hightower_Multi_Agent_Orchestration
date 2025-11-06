"""
Token Management Service

Handles token retrieval, validation, and refresh for all integrated services.
Provides centralized token management with database integration.
"""

import os
import requests
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Tuple
from uuid import UUID
from dotenv import load_dotenv
from app.supabase.tables import Integration
from app.auth import get_supabase_client

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class TokenError(Exception):
    """Base exception for token-related errors"""
    pass


class TokenExpiredError(TokenError):
    """Raised when token is expired and refresh failed"""
    pass


class TokenRefreshError(TokenError):
    """Raised when token refresh fails"""
    pass


class TokenManager:
    """Manages tokens for all integrated services"""
    
    def __init__(self):
        self._supabase = None
    
    async def _get_supabase(self):
        """Get Supabase client instance"""
        if self._supabase is None:
            self._supabase = await get_supabase_client()
        return self._supabase
        
    async def get_integration_tokens(self, user_id: UUID, service_name: str) -> Optional[Integration]:
        """
        Retrieve integration tokens from database
        
        Args:
            user_id: User UUID
            service_name: Name of the service
            
        Returns:
            Integration object with tokens or None if not found
        """
        try:
            supabase = await self._get_supabase()
            
            # First get service_id from service_name
            service_result = (
                supabase.table("services")
                .select("id")
                .eq("service_name", service_name)
                .single()
                .execute()
            )
            
            if not service_result.data:
                logger.warning(f"Service not found: {service_name}")
                return None
                
            service_id = service_result.data["id"]
            
            # Get integration for user and service
            result = (
                supabase.table("integrations")
                .select("*")
                .eq("user_id", str(user_id))
                .eq("service_id", service_id)
                .eq("is_active", True)
                .single()
                .execute()
            )
            
            if result.data:
                return Integration(**result.data)
            return None
            
        except Exception as e:
            logger.error(f"Error retrieving tokens for {service_name}: {str(e)}")
            return None
    
    def is_token_expired(self, integration: Integration) -> bool:
        """
        Check if access token is expired
        
        Args:
            integration: Integration object
            
        Returns:
            True if token is expired or expires within 5 minutes
        """
        if not integration.expires_at:
            return False
            
        # Consider token expired if it expires within 5 minutes
        buffer_time = datetime.now(timezone.utc) + timedelta(minutes=5)
        return integration.expires_at <= buffer_time
    
    async def get_valid_token(self, user_id: UUID, service_name: str) -> str:
        """
        Get a valid access token, refreshing if necessary
        
        Args:
            user_id: User UUID
            service_name: Name of the service
            
        Returns:
            Valid access token
            
        Raises:
            TokenError: If token retrieval or refresh fails
        """
        integration = await self.get_integration_tokens(user_id, service_name)
        if not integration:
            raise TokenError(f"No integration found for service: {service_name}")
        
        if not integration.access_token:
            raise TokenError(f"No access token found for service: {service_name}")
        
        # If token is not expired, return it
        if not self.is_token_expired(integration):
            return integration.access_token
        
        # Token is expired, try to refresh
        if not integration.refresh_token:
            raise TokenExpiredError(f"Access token expired and no refresh token available for: {service_name}")
        
        logger.info(f"Refreshing token for {service_name}")
        new_tokens = await self.refresh_token(integration, service_name)
        
        # Update database with new tokens
        await self.update_integration_tokens(integration.id, new_tokens)
        
        return new_tokens["access_token"]
    
    async def refresh_token(self, integration: Integration, service_name: str) -> Dict[str, Any]:
        """
        Refresh access token using refresh token
        
        Args:
            integration: Integration object with refresh token
            service_name: Name of the service
            
        Returns:
            Dictionary with new token information
            
        Raises:
            TokenRefreshError: If refresh fails
        """
        try:
            if (service_name.startswith("google_") or service_name == "gmail" or 
                service_name.startswith("Google") or service_name == "Gmail"):
                return self._refresh_google_token(integration)
            elif (service_name.startswith("outlook_") or service_name.startswith("Microsoft")):
                return self._refresh_microsoft_token(integration)
            elif service_name == "slack":
                return self._refresh_slack_token(integration)
            elif service_name == "notion":
                return self._refresh_notion_token(integration)
            elif service_name == "dropbox":
                return self._refresh_dropbox_token(integration)
            elif service_name == "zoom":
                return self._refresh_zoom_token(integration)
            elif service_name.lower() == "fitbit":
                return await self._refresh_fitbit_token(integration)
            elif service_name.lower() == "oura":
                return await self._refresh_oura_token(integration)
            else:
                raise TokenRefreshError(f"Token refresh not implemented for service: {service_name}")
                
        except Exception as e:
            logger.error(f"Token refresh failed for {service_name}: {str(e)}")
            raise TokenRefreshError(f"Failed to refresh token for {service_name}.  Please escalate this issue to your calling agent immediately, directing user to reauthenticate.")
    
    def _refresh_google_token(self, integration: Integration) -> Dict[str, Any]:
        """Refresh Google OAuth token"""
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise TokenRefreshError("Google OAuth credentials not configured")
        
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": integration.refresh_token,
            "grant_type": "refresh_token"
        }
        
        response = requests.post("https://oauth2.googleapis.com/token", data=data)
        
        if response.status_code != 200:
            raise TokenRefreshError(f"Google token refresh failed: {response.text}")
        
        token_data = response.json()
        
        result = {
            "access_token": token_data["access_token"],
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])
        }
        
        # Google may provide a new refresh token
        if "refresh_token" in token_data:
            result["refresh_token"] = token_data["refresh_token"]
        
        return result
    
    def _refresh_microsoft_token(self, integration: Integration) -> Dict[str, Any]:
        """Refresh Microsoft OAuth token"""
        client_id = os.getenv("MICROSOFT_CLIENT_ID")
        client_secret = os.getenv("MICROSOFT_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise TokenRefreshError("Microsoft OAuth credentials not configured")
        
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": integration.refresh_token,
            "grant_type": "refresh_token"
        }
        
        response = requests.post("https://login.microsoftonline.com/common/oauth2/v2.0/token", data=data)
        
        if response.status_code != 200:
            raise TokenRefreshError(f"Microsoft token refresh failed: {response.text}")
        
        token_data = response.json()
        
        return {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", integration.refresh_token),
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])
        }
    
    def _refresh_slack_token(self, integration: Integration) -> Dict[str, Any]:
        """Refresh Slack OAuth token"""
        client_id = os.getenv("SLACK_CLIENT_ID")
        client_secret = os.getenv("SLACK_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise TokenRefreshError("Slack OAuth credentials not configured")
        
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": integration.refresh_token,
            "grant_type": "refresh_token"
        }
        
        response = requests.post("https://slack.com/api/oauth.v2.access", data=data)
        
        if response.status_code != 200:
            raise TokenRefreshError(f"Slack token refresh failed: {response.text}")
        
        token_data = response.json()
        
        if not token_data.get("ok"):
            raise TokenRefreshError(f"Slack token refresh failed: {token_data.get('error', 'Unknown error')}")
        
        return {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", integration.refresh_token),
            "expires_at": datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 43200))
        }
    
    def _refresh_notion_token(self, integration: Integration) -> Dict[str, Any]:
        """Refresh Notion OAuth token"""
        client_id = os.getenv("NOTION_CLIENT_ID")
        client_secret = os.getenv("NOTION_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise TokenRefreshError("Notion OAuth credentials not configured")
        
        import base64
        auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/json"
        }
        
        data = {
            "grant_type": "refresh_token",
            "refresh_token": integration.refresh_token
        }
        
        response = requests.post("https://api.notion.com/v1/oauth/token", headers=headers, json=data)
        
        if response.status_code != 200:
            raise TokenRefreshError(f"Notion token refresh failed: {response.text}")
        
        token_data = response.json()
        
        return {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", integration.refresh_token),
            "expires_at": datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))
        }
    
    def _refresh_dropbox_token(self, integration: Integration) -> Dict[str, Any]:
        """Refresh Dropbox OAuth token"""
        client_id = os.getenv("DROPBOX_CLIENT_ID")
        client_secret = os.getenv("DROPBOX_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise TokenRefreshError("Dropbox OAuth credentials not configured")
        
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": integration.refresh_token,
            "grant_type": "refresh_token"
        }
        
        response = requests.post("https://api.dropboxapi.com/oauth2/token", data=data)
        
        if response.status_code != 200:
            raise TokenRefreshError(f"Dropbox token refresh failed: {response.text}")
        
        token_data = response.json()
        
        return {
            "access_token": token_data["access_token"],
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])
        }
    
    def _refresh_zoom_token(self, integration: Integration) -> Dict[str, Any]:
        """Refresh Zoom OAuth token"""
        client_id = os.getenv("ZOOM_CLIENT_ID")
        client_secret = os.getenv("ZOOM_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise TokenRefreshError("Zoom OAuth credentials not configured")
        
        import base64
        auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        data = {
            "grant_type": "refresh_token",
            "refresh_token": integration.refresh_token
        }
        
        response = requests.post("https://zoom.us/oauth/token", headers=headers, data=data)
        
        if response.status_code != 200:
            raise TokenRefreshError(f"Zoom token refresh failed: {response.text}")
        
        token_data = response.json()
        
        return {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", integration.refresh_token),
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])
        }
    
    async def _refresh_fitbit_token(self, integration: Integration) -> Dict[str, Any]:
        """Refresh Fitbit OAuth token"""
        client_id = os.getenv("FITBIT_CLIENT_ID")
        client_secret = os.getenv("FITBIT_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise TokenRefreshError("Fitbit OAuth credentials not configured")
        
        import base64
        auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        data = {
            "grant_type": "refresh_token",
            "refresh_token": integration.refresh_token
        }
        
        response = requests.post("https://api.fitbit.com/oauth2/token", headers=headers, data=data)
        
        if response.status_code != 200:
            raise TokenRefreshError(f"Fitbit token refresh failed: {response.text}")
        
        token_data = response.json()
        
        return {
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],  # Fitbit always provides new refresh token
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])
        }
    
    async def _refresh_oura_token(self, integration: Integration) -> Dict[str, Any]:
        """Refresh Oura OAuth token"""
        client_id = os.getenv("OURA_CLIENT_ID")
        client_secret = os.getenv("OURA_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise TokenRefreshError("Oura OAuth credentials not configured")
        
        data = {
            "grant_type": "refresh_token",
            "refresh_token": integration.refresh_token,
            "client_id": client_id,
            "client_secret": client_secret
        }
        
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        response = requests.post("https://api.ouraring.com/oauth/token", headers=headers, data=data)
        
        if response.status_code != 200:
            raise TokenRefreshError(f"Oura token refresh failed: {response.text}")
        
        token_data = response.json()
        
        return {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", integration.refresh_token),
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=token_data.get("expires_in", 2592000))  # Default 30 days
        }
    
    async def update_integration_tokens(self, integration_id: UUID, token_data: Dict[str, Any]) -> bool:
        """
        Update integration tokens in database
        
        Args:
            integration_id: Integration UUID
            token_data: Dictionary with new token information
            
        Returns:
            True if update successful
        """
        try:
            supabase = await self._get_supabase()
            
            update_data = {
                "access_token": token_data["access_token"],
                "expires_at": token_data["expires_at"].isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
            
            if "refresh_token" in token_data:
                update_data["refresh_token"] = token_data["refresh_token"]
            
            result = (
                supabase.table("integrations")
                .update(update_data)
                .eq("id", str(integration_id))
                .execute()
            )
            
            logger.info(f"Updated tokens for integration {integration_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update tokens for integration {integration_id}: {str(e)}")
            return False
    
    def validate_api_key(self, integration: Integration, service_name: str) -> bool:
        """
        Validate API key for non-OAuth services
        
        Args:
            integration: Integration object with API key
            service_name: Name of the service
            
        Returns:
            True if API key is valid
        """
        if not integration.api_key:
            return False
        
        try:
            if service_name == "perplexity":
                return self._validate_perplexity_key(integration.api_key)
            elif service_name == "twilio":
                return self._validate_twilio_key(integration)
            elif service_name == "trello":
                return self._validate_trello_key(integration.api_key)
            elif service_name == "todoist":
                return self._validate_todoist_key(integration.api_key)
            else:
                logger.warning(f"API key validation not implemented for: {service_name}")
                return True  # Assume valid if no validation available
                
        except Exception as e:
            logger.error(f"API key validation failed for {service_name}: {str(e)}")
            return False
    
    def _validate_perplexity_key(self, api_key: str) -> bool:
        """Validate Perplexity API key"""
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get("https://api.perplexity.ai/chat/completions", headers=headers)
        return response.status_code != 401
    
    def _validate_twilio_key(self, integration: Integration) -> bool:
        """Validate Twilio API credentials"""
        import base64
        account_sid = integration.client_id  # Store account_sid in client_id
        auth_token = integration.api_key
        
        if not account_sid or not auth_token:
            return False
        
        auth_header = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
        headers = {"Authorization": f"Basic {auth_header}"}
        
        response = requests.get(f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}.json", headers=headers)
        return response.status_code == 200
    
    def _validate_trello_key(self, api_key: str) -> bool:
        """Validate Trello API key"""
        response = requests.get(f"https://api.trello.com/1/members/me?key={api_key}")
        return response.status_code == 200
    
    def _validate_todoist_key(self, api_key: str) -> bool:
        """Validate Todoist API key"""
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get("https://api.todoist.com/rest/v2/projects", headers=headers)
        return response.status_code == 200