import json
import os
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Union
import re

class LogAnalyzer:
    """Utility for analyzing structured JSON logs."""
    
    def __init__(self, log_file_path: str = "logs/app.log"):
        self.log_file_path = log_file_path
        self._ensure_log_file_exists()
    
    def _ensure_log_file_exists(self):
        """Create log file if it doesn't exist."""
        if not os.path.exists(self.log_file_path):
            Path(self.log_file_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.log_file_path).touch()
    
    def _parse_log_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Parse a single log line into a dictionary."""
        try:
            return json.loads(line.strip())
        except (json.JSONDecodeError, ValueError):
            return None
    
    def get_all_logs(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get all log entries as dictionaries."""
        logs = []
        
        with open(self.log_file_path, 'r') as f:
            for line in f:
                log_entry = self._parse_log_line(line)
                if log_entry:
                    logs.append(log_entry)
                    if limit and len(logs) >= limit:
                        break
        
        return logs
    
    def filter_logs(self, **filters) -> List[Dict[str, Any]]:
        """
        Filter logs by specified criteria.
        
        Args:
            **filters: Key-value pairs to filter by (e.g., component='integration_builder', level='ERROR')
        
        Returns:
            List of matching log entries
        """
        filtered_logs = []
        
        with open(self.log_file_path, 'r') as f:
            for line in f:
                log_entry = self._parse_log_line(line)
                if not log_entry:
                    continue
                
                # Check if log entry matches all filters
                matches = True
                for key, value in filters.items():
                    if key not in log_entry or log_entry[key] != value:
                        matches = False
                        break
                
                if matches:
                    filtered_logs.append(log_entry)
        
        return filtered_logs
    
    def filter_logs_by_component(self, component: str) -> List[Dict[str, Any]]:
        """Filter logs by component name."""
        return self.filter_logs(component=component)
    
    def filter_logs_by_level(self, level: str) -> List[Dict[str, Any]]:
        """Filter logs by log level."""
        return self.filter_logs(level=level)
    
    def filter_logs_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        """Filter logs by user ID."""
        return self.filter_logs(user_id=user_id)
    
    def filter_logs_by_time_range(
        self, 
        start_time: Union[str, datetime], 
        end_time: Union[str, datetime]
    ) -> List[Dict[str, Any]]:
        """Filter logs by time range."""
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        if isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        
        filtered_logs = []
        
        with open(self.log_file_path, 'r') as f:
            for line in f:
                log_entry = self._parse_log_line(line)
                if not log_entry or 'timestamp' not in log_entry:
                    continue
                
                log_time = datetime.fromisoformat(log_entry['timestamp'].replace('Z', '+00:00'))
                if start_time <= log_time <= end_time:
                    filtered_logs.append(log_entry)
        
        return filtered_logs
    
    def search_logs(self, search_term: str, case_sensitive: bool = False) -> List[Dict[str, Any]]:
        """Search logs for a specific term in the message field."""
        pattern = re.compile(search_term if case_sensitive else search_term, 
                           0 if case_sensitive else re.IGNORECASE)
        
        matching_logs = []
        
        with open(self.log_file_path, 'r') as f:
            for line in f:
                log_entry = self._parse_log_line(line)
                if not log_entry:
                    continue
                
                # Search in message field
                message = log_entry.get('message', '')
                if pattern.search(message):
                    matching_logs.append(log_entry)
        
        return matching_logs
    
    def get_integration_logs(
        self, 
        service_name: str = None, 
        integration_id: str = None,
        user_id: str = None
    ) -> List[Dict[str, Any]]:
        """Get all integration-related logs with optional filtering."""
        filters = {'component': 'integration_builder'}
        
        if service_name:
            filters['service_name'] = service_name
        if integration_id:
            filters['integration_id'] = integration_id
        if user_id:
            filters['user_id'] = user_id
        
        return self.filter_logs(**filters)
    
    def get_agent_logs(
        self, 
        agent_name: str = None, 
        user_id: str = None
    ) -> List[Dict[str, Any]]:
        """Get all agent-related logs with optional filtering."""
        filters = {'component': 'agent'}
        
        if agent_name:
            filters['agent_name'] = agent_name
        if user_id:
            filters['user_id'] = user_id
        
        return self.filter_logs(**filters)
    
    def get_error_logs(self, component: str = None) -> List[Dict[str, Any]]:
        """Get all error logs, optionally filtered by component."""
        filters = {'level': 'ERROR'}
        if component:
            filters['component'] = component
        
        return self.filter_logs(**filters)
    
    def generate_component_summary(self, component: str) -> Dict[str, Any]:
        """Generate a summary report for a specific component."""
        logs = self.filter_logs_by_component(component)
        
        if not logs:
            return {
                'component': component,
                'total_logs': 0,
                'message': 'No logs found for this component'
            }
        
        summary = {
            'component': component,
            'total_logs': len(logs),
            'log_levels': Counter(log.get('level', 'UNKNOWN') for log in logs),
            'actions': Counter(log.get('action') for log in logs if log.get('action')),
            'users': Counter(log.get('user_id') for log in logs if log.get('user_id')),
            'time_range': {
                'start': min(log.get('timestamp') for log in logs if log.get('timestamp')),
                'end': max(log.get('timestamp') for log in logs if log.get('timestamp'))
            },
            'recent_errors': []
        }
        
        # Get recent errors for this component
        error_logs = [log for log in logs if log.get('level') == 'ERROR']
        summary['recent_errors'] = sorted(error_logs, 
                                        key=lambda x: x.get('timestamp', ''), 
                                        reverse=True)[:5]
        
        return summary
    
    def generate_overall_summary(self, hours_back: int = 24) -> Dict[str, Any]:
        """Generate an overall summary of recent log activity."""
        cutoff_time = datetime.now() - timedelta(hours=hours_back)
        recent_logs = self.filter_logs_by_time_range(cutoff_time, datetime.now())
        
        if not recent_logs:
            return {
                'total_logs': 0,
                'time_range_hours': hours_back,
                'message': 'No recent logs found'
            }
        
        summary = {
            'total_logs': len(recent_logs),
            'time_range_hours': hours_back,
            'components': Counter(log.get('component', 'unknown') for log in recent_logs),
            'log_levels': Counter(log.get('level', 'UNKNOWN') for log in recent_logs),
            'most_active_users': Counter(log.get('user_id') for log in recent_logs if log.get('user_id')),
            'common_actions': Counter(log.get('action') for log in recent_logs if log.get('action')),
            'error_rate': len([log for log in recent_logs if log.get('level') == 'ERROR']) / len(recent_logs) * 100,
            'recent_errors': []
        }
        
        # Get recent errors across all components
        error_logs = [log for log in recent_logs if log.get('level') == 'ERROR']
        summary['recent_errors'] = sorted(error_logs, 
                                        key=lambda x: x.get('timestamp', ''), 
                                        reverse=True)[:10]
        
        return summary
    
    def save_filtered_logs(
        self, 
        output_file: str, 
        **filters
    ) -> int:
        """Save filtered logs to a separate file."""
        filtered_logs = self.filter_logs(**filters)
        
        with open(output_file, 'w') as f:
            for log in filtered_logs:
                f.write(json.dumps(log, default=str) + '\n')
        
        return len(filtered_logs)
    
    def get_performance_logs(self, min_duration_seconds: float = 1.0) -> List[Dict[str, Any]]:
        """Get logs with performance data above a certain threshold."""
        performance_logs = []
        
        with open(self.log_file_path, 'r') as f:
            for line in f:
                log_entry = self._parse_log_line(line)
                if not log_entry:
                    continue
                
                duration = log_entry.get('duration_seconds')
                if duration and float(duration) >= min_duration_seconds:
                    performance_logs.append(log_entry)
        
        return sorted(performance_logs, key=lambda x: x.get('duration_seconds', 0), reverse=True)

# Convenience functions for common queries
def quick_component_logs(component: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Quick function to get recent logs for a component."""
    analyzer = LogAnalyzer()
    logs = analyzer.filter_logs_by_component(component)
    return sorted(logs, key=lambda x: x.get('timestamp', ''), reverse=True)[:limit]

def quick_error_summary() -> Dict[str, Any]:
    """Quick function to get a summary of recent errors."""
    analyzer = LogAnalyzer()
    return analyzer.generate_overall_summary(hours_back=1)

def quick_user_activity(user_id: str, hours_back: int = 24) -> List[Dict[str, Any]]:
    """Quick function to get recent activity for a specific user."""
    analyzer = LogAnalyzer()
    cutoff_time = datetime.now() - timedelta(hours=hours_back)
    user_logs = analyzer.filter_logs_by_user(user_id)
    
    # Filter by time
    recent_logs = []
    for log in user_logs:
        if log.get('timestamp'):
            log_time = datetime.fromisoformat(log['timestamp'].replace('Z', '+00:00'))
            if log_time >= cutoff_time:
                recent_logs.append(log)
    
    return sorted(recent_logs, key=lambda x: x.get('timestamp', ''), reverse=True) 