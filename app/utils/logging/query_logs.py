#!/usr/bin/env python3
"""
CLI utility for querying structured logs.

Usage examples:
  python scripts/query_logs.py --component integration_builder
  python scripts/query_logs.py --errors --last 1h
  python scripts/query_logs.py --user-id abc123 --service slack
  python scripts/query_logs.py --summary --component agent
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.utils.logging.log_analyzer import LogAnalyzer, quick_component_logs, quick_error_summary, quick_user_activity

def main():
    parser = argparse.ArgumentParser(description='Query structured application logs')
    
    # Filter options
    parser.add_argument('--component', '-c', help='Filter by component name')
    parser.add_argument('--level', '-l', help='Filter by log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)')
    parser.add_argument('--errors', action='store_true', help='Show only error logs')
    parser.add_argument('--summary', action='store_true', help='Show summary instead of individual logs')
    
    # Agent-specific filters
    parser.add_argument('--agent', '-a', help='Filter by agent name')
    parser.add_argument('--action', help='Filter by action type (e.g., service_tool_call, resources_retrieved, observation)')
    parser.add_argument('--system-prompts', action='store_true', help='Show agent system prompts')
    
    # Service tool filters
    parser.add_argument('--service-tools', action='store_true', help='Show service tool calls')
    parser.add_argument('--tool-name', help='Filter by specific tool name')
    
    # Resource filters
    parser.add_argument('--resources', action='store_true', help='Show resource retrieval logs')
    
    # User filters
    parser.add_argument('--user-id', '-u', help='Filter by user ID')
    
    # Output options
    parser.add_argument('--limit', type=int, default=50, help='Maximum number of logs to show')
    parser.add_argument('--json', action='store_true', help='Output raw JSON')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show full log details')
    
    args = parser.parse_args()
    
    # Initialize analyzer
    analyzer = LogAnalyzer('logs/app.log')
    
    try:
        # Handle special queries
        if args.summary:
            if args.component:
                summary = analyzer.generate_component_summary(args.component)
            else:
                summary = analyzer.generate_overall_summary(24)
            
            print(json.dumps(summary, indent=2, default=str))
            return
        
        # Handle system prompts query
        if args.system_prompts:
            logs = analyzer.filter_logs(action='agent_initialization')
            if args.agent:
                logs = [log for log in logs if log.get('agent_name') == args.agent]
            
            if not logs:
                print("No system prompts found.")
                return
                
            print(f"Found {len(logs)} system prompts:\n")
            for log in logs:
                timestamp = log.get('timestamp', '')[:19].replace('T', ' ')
                agent_name = log.get('agent_name', 'Unknown')
                user_id = log.get('user_id', 'Unknown')
                
                print(f"Agent: {agent_name}")
                print(f"User: {user_id}")
                print(f"Time: {timestamp}")
                print(f"System Prompt:\n{log.get('system_prompt', 'Not available')}\n")
                print("=" * 80 + "\n")
            return
        
        # Handle service tool calls query
        if args.service_tools:
            logs = analyzer.filter_logs(action='service_tool_call_start')
            success_logs = analyzer.filter_logs(action='service_tool_call_success')
            error_logs = analyzer.filter_logs(action='service_tool_call_error')
            all_tool_logs = logs + success_logs + error_logs
            
            if args.tool_name:
                all_tool_logs = [log for log in all_tool_logs if log.get('tool_name') == args.tool_name]
            
            if not all_tool_logs:
                print("No service tool calls found.")
                return
                
            print(f"Found {len(all_tool_logs)} service tool call logs:\n")
            all_tool_logs = sorted(all_tool_logs, key=lambda x: x.get('timestamp', ''), reverse=True)[:args.limit]
            
            for log in all_tool_logs:
                timestamp = log.get('timestamp', '')[:19].replace('T', ' ')
                action = log.get('action', '').replace('service_tool_call_', '')
                tool_name = log.get('tool_name', 'Unknown')
                user_id = log.get('user_id', 'Unknown')
                duration_ms = log.get('duration_ms', 'N/A')
                
                print(f"{timestamp} {action.upper():7} Tool: {tool_name} User: {user_id} Duration: {duration_ms}ms")
                
                if args.verbose:
                    if log.get('tool_parameters'):
                        print(f"  Parameters: {json.dumps(log['tool_parameters'], default=str)}")
                    if log.get('error_message'):
                        print(f"  Error: {log['error_message']}")
                    if log.get('result_preview'):
                        print(f"  Result Preview: {log['result_preview']}")
                    print()
            return
        
        # Handle resource retrieval query
        if args.resources:
            logs = analyzer.filter_logs(action='resources_retrieved')
            
            if not logs:
                print("No resource retrieval logs found.")
                return
                
            print(f"Found {len(logs)} resource retrieval logs:\n")
            logs = sorted(logs, key=lambda x: x.get('timestamp', ''), reverse=True)[:args.limit]
            
            for log in logs:
                timestamp = log.get('timestamp', '')[:19].replace('T', ' ')
                tool_name = log.get('tool_name', 'Unknown')
                user_id = log.get('user_id', 'Unknown')
                resource_count = log.get('resource_count', 0)
                
                print(f"{timestamp} Tool: {tool_name} User: {user_id} Resources: {resource_count}")
                
                if args.verbose and log.get('resource_ids'):
                    print(f"  Resource IDs: {log['resource_ids']}")
                    print()
            return
        
        # Build filters
        filters = {}
        
        if args.component:
            filters['component'] = args.component
        if args.level:
            filters['level'] = args.level.upper()
        if args.errors:
            filters['level'] = 'ERROR'
        if args.agent:
            filters['agent_name'] = args.agent
        if args.action:
            filters['action'] = args.action
        if args.user_id:
            filters['user_id'] = args.user_id
        if args.tool_name:
            filters['tool_name'] = args.tool_name
        
        # Apply filters
        if filters:
            logs = analyzer.filter_logs(**filters)
        else:
            logs = analyzer.get_all_logs()
        
        # Sort by timestamp (most recent first)
        logs = sorted(logs, key=lambda x: x.get('timestamp', ''), reverse=True)
        
        # Apply limit
        if args.limit:
            logs = logs[:args.limit]
        
        # Output results
        if not logs:
            print("No logs found matching the criteria.")
            return
        
        print(f"Found {len(logs)} log entries:\n")
        
        if args.json:
            for log in logs:
                print(json.dumps(log, default=str))
        else:
            for log in logs:
                timestamp = log.get('timestamp', '')[:19].replace('T', ' ')
                level = log.get('level', 'INFO')
                component = log.get('component', 'app')
                message = log.get('message', '')
                
                # Basic output format
                output = f"{timestamp} {level:5} [{component:15}] {message}"
                
                # Add additional context if verbose
                if args.verbose:
                    extras = []
                    if log.get('agent_name'):
                        extras.append(f"agent={log['agent_name']}")
                    if log.get('action'):
                        extras.append(f"action={log['action']}")
                    if log.get('tool_name'):
                        extras.append(f"tool={log['tool_name']}")
                    if log.get('user_id'):
                        extras.append(f"user={log['user_id']}")
                    if log.get('duration_ms'):
                        extras.append(f"duration={log['duration_ms']}ms")
                    
                    if extras:
                        output += f" [{', '.join(extras)}]"
                
                print(output)
    
    except Exception as e:
        print(f"Error querying logs: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
