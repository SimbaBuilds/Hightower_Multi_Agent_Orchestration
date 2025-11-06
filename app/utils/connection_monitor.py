"""
Connection monitoring for Supabase client
"""

import time
import threading
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from collections import deque
import statistics

logger = logging.getLogger(__name__)

@dataclass
class QueryMetric:
    """Individual query metric"""
    timestamp: float
    duration: float
    success: bool
    operation: str
    error: Optional[str] = None

@dataclass
class ConnectionStats:
    """Connection statistics"""
    total_queries: int = 0
    successful_queries: int = 0
    failed_queries: int = 0
    avg_response_time: float = 0.0
    min_response_time: float = float('inf')
    max_response_time: float = 0.0
    circuit_breaker_trips: int = 0
    last_activity: Optional[float] = None
    start_time: float = field(default_factory=time.time)

class ConnectionMonitor:
    """
    Monitor Supabase connection performance and health
    """
    
    def __init__(self, max_metrics: int = 1000):
        """
        Initialize connection monitor.
        
        Args:
            max_metrics: Maximum number of metrics to keep in memory
        """
        self.max_metrics = max_metrics
        self._metrics: deque[QueryMetric] = deque(maxlen=max_metrics)
        self._stats = ConnectionStats()
        self._lock = threading.Lock()
        
        # Performance tracking windows
        self._recent_metrics: deque[QueryMetric] = deque(maxlen=100)  # Last 100 queries
        
    def record_query(self, duration: float, success: bool, operation: str = "query", error: str = None):
        """
        Record a query execution.
        
        Args:
            duration: Query duration in seconds
            success: Whether query was successful
            operation: Type of operation (query, auth, write, etc.)
            error: Error message if failed
        """
        timestamp = time.time()
        metric = QueryMetric(
            timestamp=timestamp,
            duration=duration,
            success=success,
            operation=operation,
            error=error
        )
        
        with self._lock:
            self._metrics.append(metric)
            self._recent_metrics.append(metric)
            
            # Update stats
            self._stats.total_queries += 1
            self._stats.last_activity = timestamp
            
            if success:
                self._stats.successful_queries += 1
            else:
                self._stats.failed_queries += 1
            
            # Update response time stats
            self._stats.min_response_time = min(self._stats.min_response_time, duration)
            self._stats.max_response_time = max(self._stats.max_response_time, duration)
            
            # Calculate average (simple moving average)
            recent_durations = [m.duration for m in list(self._recent_metrics)]
            if recent_durations:
                self._stats.avg_response_time = statistics.mean(recent_durations)
    
    def record_circuit_breaker_trip(self):
        """Record a circuit breaker trip"""
        with self._lock:
            self._stats.circuit_breaker_trips += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get current connection statistics.
        
        Returns:
            Dict with connection statistics
        """
        with self._lock:
            uptime = time.time() - self._stats.start_time
            
            # Calculate success rate
            success_rate = 0.0
            if self._stats.total_queries > 0:
                success_rate = (self._stats.successful_queries / self._stats.total_queries) * 100
            
            # Get recent performance (last 5 minutes)
            recent_cutoff = time.time() - 300  # 5 minutes
            recent_metrics = [m for m in self._recent_metrics if m.timestamp >= recent_cutoff]
            
            recent_stats = {
                "count": len(recent_metrics),
                "success_rate": 0.0,
                "avg_duration": 0.0,
                "errors": []
            }
            
            if recent_metrics:
                recent_successful = sum(1 for m in recent_metrics if m.success)
                recent_stats["success_rate"] = (recent_successful / len(recent_metrics)) * 100
                recent_stats["avg_duration"] = statistics.mean([m.duration for m in recent_metrics])
                recent_stats["errors"] = [m.error for m in recent_metrics if m.error][-5:]  # Last 5 errors
            
            return {
                "uptime_seconds": uptime,
                "total_queries": self._stats.total_queries,
                "successful_queries": self._stats.successful_queries,
                "failed_queries": self._stats.failed_queries,
                "success_rate_percent": success_rate,
                "response_time": {
                    "avg_ms": round(self._stats.avg_response_time * 1000, 2),
                    "min_ms": round(self._stats.min_response_time * 1000, 2) if self._stats.min_response_time != float('inf') else 0,
                    "max_ms": round(self._stats.max_response_time * 1000, 2)
                },
                "circuit_breaker_trips": self._stats.circuit_breaker_trips,
                "last_activity": self._stats.last_activity,
                "recent_performance": recent_stats
            }
    
    def get_health_status(self) -> Dict[str, Any]:
        """
        Get health status with recommendations.
        
        Returns:
            Dict with health status and recommendations
        """
        stats = self.get_stats()
        health_status = "healthy"
        issues = []
        recommendations = []
        
        # Check success rate
        if stats["success_rate_percent"] < 95:
            health_status = "degraded"
            issues.append(f"Low success rate: {stats['success_rate_percent']:.1f}%")
            recommendations.append("Check database connectivity and query patterns")
        
        # Check response times
        if stats["response_time"]["avg_ms"] > 5000:  # 5 seconds
            health_status = "degraded"
            issues.append(f"High response time: {stats['response_time']['avg_ms']:.0f}ms")
            recommendations.append("Consider optimizing queries or checking network latency")
        
        # Check circuit breaker trips
        if stats["circuit_breaker_trips"] > 0:
            if stats["circuit_breaker_trips"] > 5:
                health_status = "unhealthy"
            issues.append(f"Circuit breaker trips: {stats['circuit_breaker_trips']}")
            recommendations.append("Investigate recurring connection issues")
        
        # Check recent activity
        if stats["last_activity"]:
            idle_time = time.time() - stats["last_activity"]
            if idle_time > 300:  # 5 minutes
                issues.append(f"No recent activity: {idle_time:.0f}s ago")
        
        return {
            "status": health_status,
            "issues": issues,
            "recommendations": recommendations,
            "stats": stats
        }
    
    def get_operation_breakdown(self) -> Dict[str, Dict[str, Any]]:
        """
        Get performance breakdown by operation type.
        
        Returns:
            Dict with stats per operation type
        """
        with self._lock:
            operations: Dict[str, List[QueryMetric]] = {}
            
            # Group metrics by operation
            for metric in self._recent_metrics:
                if metric.operation not in operations:
                    operations[metric.operation] = []
                operations[metric.operation].append(metric)
            
            # Calculate stats per operation
            result = {}
            for operation, metrics in operations.items():
                successful = sum(1 for m in metrics if m.success)
                durations = [m.duration for m in metrics]
                
                result[operation] = {
                    "count": len(metrics),
                    "success_rate": (successful / len(metrics)) * 100,
                    "avg_duration_ms": round(statistics.mean(durations) * 1000, 2),
                    "max_duration_ms": round(max(durations) * 1000, 2),
                    "min_duration_ms": round(min(durations) * 1000, 2)
                }
            
            return result

# Global monitor instance
_connection_monitor = ConnectionMonitor()

def get_connection_monitor() -> ConnectionMonitor:
    """Get the global connection monitor instance"""
    return _connection_monitor

def record_query_metric(duration: float, success: bool, operation: str = "query", error: str = None):
    """Convenience function to record query metrics"""
    _connection_monitor.record_query(duration, success, operation, error)

def record_circuit_breaker_trip():
    """Convenience function to record circuit breaker trips"""
    _connection_monitor.record_circuit_breaker_trip()

def get_connection_stats() -> Dict[str, Any]:
    """Convenience function to get connection stats"""
    return _connection_monitor.get_stats()

def get_connection_health() -> Dict[str, Any]:
    """Convenience function to get connection health"""
    return _connection_monitor.get_health_status()