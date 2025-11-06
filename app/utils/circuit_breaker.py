"""
Circuit breaker pattern for Supabase operations
"""

import time
import logging
import threading
from enum import Enum
from typing import Callable, Any, Optional, Dict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Circuit breaker is open, failing fast
    HALF_OPEN = "half_open"  # Testing if service is back

@dataclass
class CircuitBreakerStats:
    """Statistics for circuit breaker monitoring"""
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    state_changed_time: float = field(default_factory=time.time)
    total_requests: int = 0

class CircuitBreaker:
    """
    Circuit breaker implementation for database operations.
    
    Prevents cascading failures by failing fast when error threshold is exceeded.
    """
    
    def __init__(
        self, 
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        expected_exception: type = Exception,
        name: str = "default"
    ):
        """
        Initialize circuit breaker.
        
        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before trying again
            expected_exception: Exception type to catch
            name: Name for logging and monitoring
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.name = name
        
        self._state = CircuitState.CLOSED
        self._stats = CircuitBreakerStats()
        self._lock = threading.Lock()
        
        logger.info(f"Circuit breaker '{name}' initialized with threshold {failure_threshold}")
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state"""
        return self._state
    
    @property
    def stats(self) -> CircuitBreakerStats:
        """Get circuit breaker statistics"""
        with self._lock:
            return CircuitBreakerStats(
                failure_count=self._stats.failure_count,
                success_count=self._stats.success_count,
                last_failure_time=self._stats.last_failure_time,
                last_success_time=self._stats.last_success_time,
                state_changed_time=self._stats.state_changed_time,
                total_requests=self._stats.total_requests
            )
    
    def _change_state(self, new_state: CircuitState) -> None:
        """Change circuit state with logging"""
        if self._state != new_state:
            old_state = self._state
            self._state = new_state
            self._stats.state_changed_time = time.time()
            
            logger.warning(
                f"Circuit breaker '{self.name}' state changed: {old_state.value} -> {new_state.value}"
            )
    
    def _should_attempt_reset(self) -> bool:
        """Check if circuit should attempt to reset"""
        return (
            self._stats.last_failure_time and 
            time.time() - self._stats.last_failure_time >= self.recovery_timeout
        )
    
    def _record_success(self) -> None:
        """Record successful operation"""
        with self._lock:
            self._stats.success_count += 1
            self._stats.last_success_time = time.time()
            self._stats.total_requests += 1
            
            if self._state == CircuitState.HALF_OPEN:
                # Successful call in half-open state, close the circuit
                self._stats.failure_count = 0  # Reset failure count
                self._change_state(CircuitState.CLOSED)
                logger.info(f"Circuit breaker '{self.name}' reset to CLOSED after successful call")
    
    def _record_failure(self, exception: Exception) -> None:
        """Record failed operation"""
        with self._lock:
            self._stats.failure_count += 1
            self._stats.last_failure_time = time.time()
            self._stats.total_requests += 1
            
            logger.warning(
                f"Circuit breaker '{self.name}' recorded failure #{self._stats.failure_count}: {exception}"
            )
            
            if self._state == CircuitState.HALF_OPEN:
                # Failure in half-open state, go back to open
                self._change_state(CircuitState.OPEN)
                # Record circuit breaker trip in monitor
                try:
                    from app.utils.connection_monitor import record_circuit_breaker_trip
                    record_circuit_breaker_trip()
                except ImportError:
                    pass
            elif self._state == CircuitState.CLOSED and self._stats.failure_count >= self.failure_threshold:
                # Too many failures, open the circuit
                self._change_state(CircuitState.OPEN)
                # Record circuit breaker trip in monitor
                try:
                    from app.utils.connection_monitor import record_circuit_breaker_trip
                    record_circuit_breaker_trip()
                except ImportError:
                    pass
    
    def __call__(self, func: Callable) -> Callable:
        """Decorator to wrap functions with circuit breaker"""
        def wrapper(*args, **kwargs):
            return self.call(func, *args, **kwargs)
        return wrapper
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.
        
        Args:
            func: Function to execute
            *args, **kwargs: Function arguments
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerOpenException: When circuit is open
            Original exception: When function fails
        """
        with self._lock:
            current_state = self._state
        
        # Check if circuit is open
        if current_state == CircuitState.OPEN:
            if self._should_attempt_reset():
                # Try to transition to half-open
                with self._lock:
                    if self._state == CircuitState.OPEN:  # Double-check
                        self._change_state(CircuitState.HALF_OPEN)
                        current_state = CircuitState.HALF_OPEN
            else:
                # Circuit is open and not ready for retry
                raise CircuitBreakerOpenException(
                    f"Circuit breaker '{self.name}' is OPEN. "
                    f"Last failure: {time.time() - self._stats.last_failure_time:.1f}s ago. "
                    f"Will retry in {self.recovery_timeout - (time.time() - self._stats.last_failure_time):.1f}s"
                )
        
        # Execute the function with monitoring
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            duration = time.time() - start_time
            
            # Record successful operation in monitor
            try:
                from app.utils.connection_monitor import record_query_metric
                record_query_metric(duration, True, self.name.replace("supabase_", ""))
            except ImportError:
                pass
            
            self._record_success()
            return result
            
        except self.expected_exception as e:
            duration = time.time() - start_time
            
            # Record failed operation in monitor
            try:
                from app.utils.connection_monitor import record_query_metric
                record_query_metric(duration, False, self.name.replace("supabase_", ""), str(e))
            except ImportError:
                pass
            
            self._record_failure(e)
            raise
        except Exception as e:
            duration = time.time() - start_time
            
            # Record unexpected exception in monitor
            try:
                from app.utils.connection_monitor import record_query_metric
                record_query_metric(duration, False, self.name.replace("supabase_", ""), str(e))
            except ImportError:
                pass
            
            # Unexpected exception, record but don't open circuit
            logger.error(f"Circuit breaker '{self.name}' caught unexpected exception: {e}")
            raise

class CircuitBreakerOpenException(Exception):
    """Exception raised when circuit breaker is open"""
    pass

class SupabaseCircuitBreaker:
    """
    Pre-configured circuit breakers for different Supabase operations
    """
    
    def __init__(self, config=None):
        self.config = config or {}
        
        # Different circuit breakers for different operation types
        self.auth_breaker = CircuitBreaker(
            failure_threshold=self.config.get('auth_threshold', 5),
            recovery_timeout=self.config.get('auth_recovery', 30),
            name="supabase_auth"
        )
        
        self.query_breaker = CircuitBreaker(
            failure_threshold=self.config.get('query_threshold', 10),
            recovery_timeout=self.config.get('query_recovery', 15),
            name="supabase_query"
        )
        
        self.write_breaker = CircuitBreaker(
            failure_threshold=self.config.get('write_threshold', 3),
            recovery_timeout=self.config.get('write_recovery', 60),
            name="supabase_write"
        )
    
    def protect_auth(self, func: Callable) -> Any:
        """Protect authentication operations"""
        return self.auth_breaker.call(func)
    
    def protect_query(self, func: Callable) -> Any:
        """Protect read operations"""
        return self.query_breaker.call(func)
    
    def protect_write(self, func: Callable) -> Any:
        """Protect write operations"""
        return self.write_breaker.call(func)
    
    def get_status(self) -> Dict[str, Any]:
        """Get status of all circuit breakers"""
        return {
            "auth": {
                "state": self.auth_breaker.state.value,
                "stats": self.auth_breaker.stats.__dict__
            },
            "query": {
                "state": self.query_breaker.state.value,
                "stats": self.query_breaker.stats.__dict__
            },
            "write": {
                "state": self.write_breaker.state.value,
                "stats": self.write_breaker.stats.__dict__
            }
        }

# Global circuit breaker instance
_circuit_breaker = SupabaseCircuitBreaker()

def get_circuit_breaker() -> SupabaseCircuitBreaker:
    """Get the global Supabase circuit breaker instance"""
    return _circuit_breaker