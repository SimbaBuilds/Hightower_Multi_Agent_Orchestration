"""
Thread-safe singleton Supabase client with optimized connection pooling
"""

import threading
import logging
import time
from typing import Optional, Dict, Any
import httpx
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions
from httpx import HTTPTransport, Limits

from app.config.supabase_config import get_supabase_config, SupabaseConfig
from app.utils.connection_monitor import get_connection_monitor

logger = logging.getLogger(__name__)

class SupabaseSingleton:
    """Thread-safe singleton for Supabase client with connection pooling"""
    
    _instance: Optional['SupabaseSingleton'] = None
    _lock = threading.Lock()
    _client: Optional[Client] = None
    _config: Optional[SupabaseConfig] = None
    _created_at: Optional[float] = None
    _connection_count: int = 0
    _query_count: int = 0
    _last_activity: Optional[float] = None
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def _create_client(self) -> Client:
        """Create optimized Supabase client with connection pooling"""
        self._config = get_supabase_config()
        
        logger.info(f"Creating Supabase client for environment: {self._config.pool_config}")
        
        # Configure httpx transport with connection pooling
        transport = HTTPTransport(
            retries=self._config.retry_attempts,
            limits=Limits(
                max_connections=self._config.pool_config.max_connections,
                max_keepalive_connections=self._config.pool_config.max_keepalive_connections,
                keepalive_expiry=self._config.pool_config.keepalive_expiry,
            )
        )
        
        # Create optimized httpx client
        httpx_client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(
                connect=self._config.pool_config.connect_timeout,
                read=self._config.pool_config.read_timeout,
                write=self._config.pool_config.write_timeout,
                pool=self._config.pool_config.pool_timeout
            ),
            follow_redirects=True,
        )
        
        # Configure Supabase client options
        options = ClientOptions(
            postgrest_client_timeout=self._config.query_timeout,
            storage_client_timeout=self._config.query_timeout,
            auto_refresh_token=False,  # Service role doesn't need refresh
            persist_session=False,     # No session persistence needed
        )
        
        # Create Supabase client
        client = create_client(
            self._config.url,
            self._config.service_role_key,
            options=options
        )
        
        # Apply optimized connection settings to the existing session
        if hasattr(client.postgrest, 'session'):
            existing_session = client.postgrest.session
            # Copy our optimized timeout settings
            if hasattr(existing_session, 'timeout'):
                existing_session.timeout = httpx_client.timeout
            logger.info("Applied optimized timeout settings to Supabase client session")
        
        # Close our temporary client
        httpx_client.close()
        
        # Warmup connection to avoid cold start latency
        try:
            warmup_start = time.time()
            client.from_('user_profiles').select('id').limit(1).execute()
            warmup_duration = time.time() - warmup_start
            logger.info(f"Connection warmup completed in {warmup_duration*1000:.2f}ms")
        except Exception as e:
            logger.warning(f"Connection warmup failed (will retry on first use): {e}")
        
        self._created_at = time.time()
        self._last_activity = time.time()
        
        logger.info(f"Supabase singleton client created successfully with {self._config.pool_config.max_connections} max connections")
        
        return client
    
    def get_client(self) -> Client:
        """
        Get the singleton Supabase client, creating it if necessary.
        Thread-safe and optimized for connection reuse.
        
        Returns:
            Client: The Supabase client instance
        """
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = self._create_client()
        
        # Update activity tracking
        self._last_activity = time.time()
        self._query_count += 1
        
        return self._client
    
    def reset_client(self) -> None:
        """
        Reset the client connection (useful for error recovery).
        Thread-safe operation.
        """
        with self._lock:
            if self._client and hasattr(self._client.postgrest, 'session'):
                try:
                    # Close the httpx session (which closes the connection pool)
                    self._client.postgrest.session.close()
                    logger.info("Closed existing Supabase client connection and connection pool")
                except Exception as e:
                    logger.warning(f"Error closing Supabase client: {e}")
            
            self._client = None
            self._created_at = None
            logger.info("Supabase singleton client reset")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get connection statistics for monitoring.
        
        Returns:
            Dict with connection statistics
        """
        with self._lock:
            uptime = time.time() - self._created_at if self._created_at else 0
            idle_time = time.time() - self._last_activity if self._last_activity else 0
            
            stats = {
                "client_created": self._created_at is not None,
                "uptime_seconds": uptime,
                "idle_time_seconds": idle_time,
                "query_count": self._query_count,
                "config": {
                    "max_connections": self._config.pool_config.max_connections if self._config else None,
                    "keepalive_connections": self._config.pool_config.max_keepalive_connections if self._config else None,
                    "query_timeout": self._config.query_timeout if self._config else None,
                } if self._config else None
            }
            
            return stats
    
    def health_check(self) -> Dict[str, Any]:
        """
        Perform a health check on the Supabase connection.
        
        Returns:
            Dict with health check results
        """
        monitor = get_connection_monitor()
        
        try:
            client = self.get_client()
            start_time = time.time()
            
            # Simple query to test connection
            response = client.from_('user_profiles').select('id').limit(1).execute()
            
            duration = time.time() - start_time
            
            # Record successful health check
            monitor.record_query(duration, True, "health_check")
            
            return {
                "healthy": True,
                "response_time_ms": round(duration * 1000, 2),
                "query_successful": True,
                "connection_active": True,
                "timestamp": time.time()
            }
            
        except Exception as e:
            duration = time.time() - start_time if 'start_time' in locals() else 0
            
            # Record failed health check
            monitor.record_query(duration, False, "health_check", str(e))
            
            logger.error(f"Supabase health check failed: {e}")
            return {
                "healthy": False,
                "error": str(e),
                "connection_active": False,
                "timestamp": time.time()
            }

# Global singleton instance
_supabase_singleton = SupabaseSingleton()

def get_supabase_client() -> Client:
    """
    Get the optimized Supabase client singleton.
    
    Returns:
        Client: Thread-safe Supabase client with connection pooling
    """
    return _supabase_singleton.get_client()

def reset_supabase_client() -> None:
    """Reset the Supabase client (for error recovery)"""
    _supabase_singleton.reset_client()

def get_supabase_stats() -> Dict[str, Any]:
    """Get Supabase connection statistics"""
    return _supabase_singleton.get_stats()

def supabase_health_check() -> Dict[str, Any]:
    """Perform Supabase connection health check"""
    return _supabase_singleton.health_check()

# Async wrapper for compatibility
async def get_supabase_client_async() -> Client:
    """Get Supabase client for async contexts"""
    return get_supabase_client()