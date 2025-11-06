#!/usr/bin/env python3
"""
Script to populate service_tools table with action definitions from service tool files.

This script:
1. Extracts action definitions from service tool files
2. Populates the service_tools table with name, description, parameters, returns, and example
3. Creates display_name field with format "ServiceName ToolName"
4. Creates tool tags of type service_tool with display names
5. Links service_tool records to their corresponding tags
"""

import sys
import os
import json
import uuid
import argparse
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

# Add the app directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.supabase.supabase_admin.supabase_admin import SupabaseAdmin
from app.supabase.tables import Service, ServiceTool, Tag, TagType
from app.agents.integrations.service_tools.registry import service_registry


def extract_service_name_from_tool_name(tool_name: str) -> str:
    """Extract service name from tool name (e.g., 'gmail_send_email' -> 'Gmail')"""
    service_mapping = {
        'gmail': 'Gmail',
        'google_calendar': 'Google Calendar',
        'google_sheets': 'Google Sheets',
        'google_docs': 'Google Docs',
        'google_meet': 'Google Meet',
        'outlook_mail': 'Microsoft Outlook Mail',
        'outlook_calendar': 'Microsoft Outlook Calendar',
        'excel': 'Microsoft Excel Online',
        'word': 'Microsoft Word Online',
        'teams': 'Microsoft Teams',
        'slack': 'Slack',
        'notion': 'Notion',
        'todoist': 'Todoist',
        'perplexity': 'Perplexity',
        'textbelt': 'Textbelt',
        'twitter': 'Twitter/X',
        'fitbit': 'Fitbit',
        'oura': 'Oura',
        'apple_health': 'Apple Health',
        'google_health': 'Google Health Connect'
        
    }
    
    for service_key, service_name in service_mapping.items():
        if tool_name.startswith(service_key):
            return service_name
    
    # Fallback: capitalize first part of tool name
    return tool_name.split('_')[0].capitalize()


def extract_tool_name_from_full_name(tool_name: str) -> str:
    """Extract tool name from full name (e.g., 'gmail_send_email' -> 'Send Email')"""
    parts = tool_name.split('_')
    if len(parts) > 1:
        # Remove service prefix and capitalize remaining parts
        tool_parts = parts[1:]
        return ' '.join(word.capitalize() for word in tool_parts)
    return tool_name.capitalize()


def get_service_id_by_name(admin: SupabaseAdmin, service_name: str) -> Optional[str]:
    """Get service ID by service name"""
    try:
        response = admin.supabase.table('services').select('id').eq('service_name', service_name).execute()
        if response.data:
            return response.data[0]['id']
        return None
    except Exception as e:
        print(f"Error getting service ID for {service_name}: {e}")
        return None


def get_existing_service_id(admin: SupabaseAdmin, service_name: str) -> Optional[str]:
    """Get existing service ID by name, return None if not found"""
    try:
        response = admin.supabase.table('services').select('id').eq('service_name', service_name).execute()
        if response.data:
            return response.data[0]['id']
        else:
            print(f"Warning: Service '{service_name}' not found in database")
            return None
    except Exception as e:
        print(f"Error getting service ID for {service_name}: {e}")
        return None


def tool_already_exists(admin: SupabaseAdmin, service_id: str, tool_name: str) -> bool:
    """Check if a service tool already exists"""
    try:
        response = admin.supabase.table('service_tools').select('id').eq('service_id', service_id).eq('name', tool_name).execute()
        return len(response.data) > 0
    except Exception as e:
        print(f"Error checking if tool exists {tool_name}: {e}")
        return False


def create_tag_if_not_exists(admin: SupabaseAdmin, tag_name: str, tag_type: str) -> str:
    """Create tag if it doesn't exist and return its ID"""
    try:
        # Check if tag already exists
        response = admin.supabase.table('tags').select('id').eq('name', tag_name).eq('type', tag_type).execute()
        if response.data:
            return response.data[0]['id']
        
        # Create new tag
        tag_data = {
            'id': str(uuid.uuid4()),
            'name': tag_name,
            'type': tag_type,
            'created_at': datetime.now().isoformat()
        }
        
        result = admin.create_record('tags', tag_data)
        print(f"Created tag: {tag_name} (type: {tag_type})")
        return result['id']
    except Exception as e:
        print(f"Error creating tag {tag_name}: {e}")
        raise


def get_tool_definitions_from_modules():
    """Extract tool definitions from service tool modules"""
    import importlib
    import inspect
    from pathlib import Path
    
    tools_data = []
    
    # Service module mappings and their tool list variable names
    service_modules = {
        'gmail_tools': ('Gmail', 'get_gmail_tools'),
        'google_calendar_tools': ('Google Calendar', 'get_google_calendar_tools'), 
        'google_sheets_tools': ('Google Sheets', 'get_google_sheets_tools'),
        'google_docs_tools': ('Google Docs', 'get_google_docs_tools'),
        'google_meet_tools': ('Google Meet', 'get_google_meet_tools'),
        'outlook_mail_tools': ('Microsoft Outlook Mail', 'get_outlook_mail_tools'),
        'outlook_calendar_tools': ('Microsoft Outlook Calendar', 'get_outlook_calendar_tools'),
        'excel_tools': ('Microsoft Excel Online', 'EXCEL_TOOLS'),
        'word_tools': ('Microsoft Word Online', 'get_word_tools'),
        'teams_tools': ('Microsoft Teams', 'get_teams_tools'),
        'slack_tools': ('Slack', 'get_slack_tools'),
        'notion_tools': ('Notion', 'get_notion_tools'),
        'todoist_tools': ('Todoist', 'get_todoist_tools'),
        'perplexity_tools': ('Perplexity', 'get_perplexity_tools'),
        'textbelt_tools': ('Textbelt', 'get_textbelt_tools'),
        'twitter_x_tools': ('Twitter/X', 'get_twitter_tools'),
        'fitbit_tools': ('Fitbit', 'get_fitbit_tools'),
        'oura_tools': ('Oura', 'get_oura_tools'),
        'apple_health_tools': ('Apple Health', 'get_apple_health_tools'),
        'google_health_tools': ('Google Health Connect', 'get_google_health_tools')
    }
    
    for module_name, (service_name, tools_attr) in service_modules.items():
        try:
            module_path = f"app.agents.integrations.service_tools.{module_name}"
            module = importlib.import_module(module_path)
            
            # Get the tools list/function
            if hasattr(module, tools_attr):
                tools_obj = getattr(module, tools_attr)
                
                # If it's a list (like EXCEL_TOOLS), use it directly
                if isinstance(tools_obj, list):
                    tools_list = tools_obj
                else:
                    # If it's a function, call it with a dummy user_id to get the tools
                    try:
                        tools_list = tools_obj("dummy_user_id")
                    except Exception as e:
                        print(f"Error calling {tools_attr} for {module_name}: {e}")
                        continue
                
                # Process each tool
                for tool in tools_list:
                    if hasattr(tool, 'name') and hasattr(tool, 'description'):
                        tool_name = extract_tool_name_from_full_name(tool.name)
                        display_name = f"{service_name} {tool_name}"
                        
                        tools_data.append({
                            'service_name': service_name,
                            'tool_name': tool.name,
                            'display_name': display_name,
                            'description': tool.description,
                            'parameters': getattr(tool, 'parameters', None),
                            'returns': getattr(tool, 'returns', None),
                            'example': getattr(tool, 'example', None)
                        })
            else:
                print(f"Warning: {tools_attr} not found in {module_name}")
                    
        except Exception as e:
            print(f"Error processing module {module_name}: {e}")
            continue
    
    return tools_data


def update_service_tool(admin: SupabaseAdmin, service_id: str, tool_name: str, tool_data: Dict[str, Any]) -> Dict[str, Any]:
    """Update an existing service tool with new data"""
    try:
        # Get existing tool ID
        response = admin.supabase.table('service_tools').select('id').eq('service_id', service_id).eq('name', tool_name).execute()
        if not response.data:
            raise Exception(f"Tool {tool_name} not found for service {service_id}")
        
        tool_id = response.data[0]['id']
        
        # Prepare update data
        update_data = {
            'display_name': tool_data['display_name'],
            'description': tool_data['description'],
            'parameters': tool_data.get('parameters'),
            'returns': tool_data.get('returns'),  # Store as plain string
            'example': tool_data.get('example'),  # Store as plain string
            'updated_at': datetime.now().isoformat()
        }
        
        # Update the record
        result = admin.update_record('service_tools', tool_id, update_data)
        print(f"  ✓ Updated service tool: {tool_data['display_name']}")
        return result
    except Exception as e:
        print(f"  ✗ Error updating tool {tool_name}: {e}")
        raise


def populate_service_tools(skip_existing=False):
    """Main function to populate service_tools table"""
    admin = SupabaseAdmin()
    
    # Get tool definitions from modules
    all_tools = get_tool_definitions_from_modules()
    
    print(f"Found {len(all_tools)} tools to process")
    if skip_existing:
        print("Running in SKIP mode - will skip existing records")
    else:
        print("Running in UPSERT mode - will update existing records and create new ones")
    
    processed_tools = []
    updated_tools = []
    created_tags = []
    skipped_tools = []
    failed_tools = []
    
    for tool_data in all_tools:
        try:
            service_name = tool_data['service_name']
            tool_name = tool_data['tool_name']
            display_name = tool_data['display_name']
            
            print(f"Processing: {tool_name} -> {display_name}")
            
            # Get existing service ID
            service_id = get_existing_service_id(admin, service_name)
            if not service_id:
                print(f"  ✗ Skipping tool {tool_name}: Service '{service_name}' not found")
                failed_tools.append({
                    'tool_name': tool_name,
                    'display_name': display_name,
                    'error': f"Service '{service_name}' not found"
                })
                continue
            
            # Check if tool already exists
            tool_exists = tool_already_exists(admin, service_id, tool_name)
            
            if tool_exists and skip_existing:
                print(f"  ⏭ Skipping tool {tool_name}: Already exists")
                skipped_tools.append({
                    'tool_name': tool_name,
                    'display_name': display_name,
                    'service_name': service_name
                })
                continue
            elif tool_exists and not skip_existing:
                # Update existing tool (default behavior)
                try:
                    result = update_service_tool(admin, service_id, tool_name, tool_data)
                    updated_tools.append({
                        'tool_id': result['id'],
                        'name': tool_name,
                        'display_name': display_name,
                        'service_name': service_name
                    })
                except Exception as e:
                    failed_tools.append({
                        'tool_name': tool_name,
                        'display_name': display_name,
                        'error': f"Update failed: {str(e)}"
                    })
                continue
            elif not tool_exists and not skip_existing:
                # In upsert mode, create tools that don't exist (default behavior)
                print(f"  ✨ Creating new tool {tool_name} (upsert mode)")
            
            # Create tag for this tool
            tag_id = create_tag_if_not_exists(admin, display_name, TagType.SERVICE_TOOL.value)
            created_tags.append({
                'tag_id': tag_id,
                'name': display_name,
                'type': TagType.SERVICE_TOOL.value
            })
            
            # Prepare parameters as JSON (keep as is)
            parameters_json = tool_data.get('parameters')
            
            # Store returns as plain string (matching source code format)
            returns_json = tool_data.get('returns')
            
            # Store example as plain string (matching source code format)
            example_json = tool_data.get('example')
            
            # Create service tool record
            service_tool_data = {
                'id': str(uuid.uuid4()),
                'service_id': service_id,
                'name': tool_name,
                'display_name': display_name,
                'description': tool_data['description'],
                'parameters': parameters_json,
                'returns': returns_json,
                'example': example_json,
                'is_active': True,
                'tag_id': tag_id,
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            }
            
            # Create the service tool record
            result = admin.create_record('service_tools', service_tool_data)
            processed_tools.append({
                'tool_id': result['id'],
                'name': tool_name,
                'display_name': display_name,
                'service_name': service_name
            })
            
            print(f"  ✓ Created service tool: {display_name}")
            
        except Exception as e:
            print(f"  ✗ Error processing tool {tool_data.get('tool_name', 'unknown')}: {e}")
            failed_tools.append({
                'tool_name': tool_data.get('tool_name', 'unknown'),
                'display_name': tool_data.get('display_name', 'unknown'),
                'error': str(e)
            })
            continue
    
    # Print summary
    print(f"\n=== SUMMARY ===")
    print(f"Tools successfully created: {len(processed_tools)}")
    print(f"Tools successfully updated: {len(updated_tools)}")
    print(f"Tools skipped: {len(skipped_tools)}")
    print(f"Tools failed: {len(failed_tools)}")
    print(f"Tags created: {len(created_tags)}")
    
    all_processed = processed_tools + updated_tools
    if all_processed:
        print(f"Services involved: {len(set(tool['service_name'] for tool in all_processed))}")
    
    if processed_tools:
        print(f"\n✓ Successfully created tools:")
        for tool in processed_tools:
            print(f"  - {tool['display_name']} ({tool['name']})")
    
    if updated_tools:
        print(f"\n✓ Successfully updated tools:")
        for tool in updated_tools:
            print(f"  - {tool['display_name']} ({tool['name']})")
    
    if skipped_tools:
        print(f"\n⏭ Skipped tools (already exist):")
        for tool in skipped_tools:
            print(f"  - {tool['display_name']} ({tool['tool_name']})")
    
    if failed_tools:
        print(f"\n✗ Failed tools:")
        for tool in failed_tools:
            print(f"  - {tool['display_name']} ({tool['tool_name']}): {tool['error']}")
    
    if created_tags:
        print(f"\n📋 Created tags:")
        for tag in created_tags:
            print(f"  - {tag['name']} (type: {tag['type']})")
    
    return processed_tools, updated_tools, created_tags, skipped_tools, failed_tools


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Populate service_tools table with action definitions (default: upsert mode)')
    parser.add_argument('--skip-existing', action='store_true', 
                        help='Skip existing records instead of updating them (old default behavior)')
    args = parser.parse_args()
    
    try:
        print("Starting service tools population...")
        processed_tools, updated_tools, created_tags, skipped_tools, failed_tools = populate_service_tools(skip_existing=args.skip_existing)
        
        total_successful = len(processed_tools) + len(updated_tools)
        
        if failed_tools:
            print(f"\n⚠ Completed with {len(failed_tools)} failures. Successfully processed {total_successful} tools (created: {len(processed_tools)}, updated: {len(updated_tools)}), skipped {len(skipped_tools)} tools.")
            return 1 if len(failed_tools) > total_successful else 0  # Return error if more failures than successes
        else:
            if args.skip_existing:
                print(f"\n✓ Successfully populated service_tools table with {len(processed_tools)} new tools (skipped {len(skipped_tools)} existing)")
            else:
                print(f"\n✓ Successfully processed {total_successful} tools (created: {len(processed_tools)}, updated: {len(updated_tools)}) in service_tools table")
            return 0
    except Exception as e:
        print(f"✗ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())