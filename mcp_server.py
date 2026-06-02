#!/usr/bin/env python3
import os
import json
import logging
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional, List, Dict, Any

import httpx
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from starlette.types import Scope, Receive, Send
from dotenv import dotenv_values

# MCP SDK
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.sse import SseServerTransport

# Google API Client Libraries
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest

# Configure Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("mcp_google_server")

# Dynamic mappings
session_to_profile: Dict[str, str] = {}

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar",
]

# Initialize FastMCP
mcp = FastMCP("Google Workspace MCP Server")

# Helper: Find profile by token
def get_profile_by_token(token: str) -> Optional[str]:
    profiles_dir = "profiles"
    if not os.path.exists(profiles_dir):
        return None
        
    for name in os.listdir(profiles_dir):
        profile_dir = os.path.join(profiles_dir, name)
        if os.path.isdir(profile_dir):
            env_path = os.path.join(profile_dir, ".env")
            if os.path.exists(env_path):
                env_vals = dotenv_values(env_path)
                p_token = env_vals.get("PROFILE_TOKEN") or env_vals.get("CALENDAR_PROFILE_TOKEN")
                if p_token == token:
                    return name
    return None

# Helper: Get credentials for a profile
def get_profile_credentials(profile_name: str) -> Credentials:
    profile_dir = os.path.join("profiles", profile_name)
    env_path = os.path.join(profile_dir, ".env")
    env_data = dotenv_values(env_path) if os.path.exists(env_path) else {}
    
    creds_path = "google_cli_client.json"
    token_path = "google_calendar_token.json"
    
    accounts_str = env_data.get("CALENDAR_ACCOUNTS")
    if accounts_str:
        try:
            accounts = json.loads(accounts_str)
            for acc in accounts:
                if acc.get("type") == "google":
                    creds_path = acc.get("credentials_path", creds_path)
                    token_path = acc.get("token_path", token_path)
                    break
        except Exception as e:
            logger.warning(f"Error parsing CALENDAR_ACCOUNTS for profile {profile_name}: {e}")
            
    creds_full = os.path.join(profile_dir, creds_path) if not os.path.isabs(creds_path) else creds_path
    token_full = os.path.join(profile_dir, token_path) if not os.path.isabs(token_path) else token_path
    
    if not os.path.exists(token_full):
        raise ValueError(f"Credentials token file not found at: {token_full}. Please run 'python cli.py oauth --profile {profile_name}' first.")
        
    try:
        creds = Credentials.from_authorized_user_file(token_full)
        
        # Refresh token if expired
        if creds and creds.expired and creds.refresh_token:
            logger.info(f"Refreshing expired Google credentials for profile '{profile_name}'")
            if os.path.exists(creds_full):
                with open(creds_full) as f:
                    client_info = json.load(f)
                    client_type = "installed" if "installed" in client_info else "web"
                    creds.client_id = client_info[client_type]["client_id"]
                    creds.client_secret = client_info[client_type]["client_secret"]
            creds.refresh(GoogleAuthRequest())
            with open(token_full, "w") as f:
                f.write(creds.to_json())
    except Exception as e:
        logger.error(f"Error loading/refreshing credentials for profile '{profile_name}': {e}")
        raise ValueError(
            f"Failed to authenticate or refresh Google credentials. "
            f"Please run the OAuth flow: 'python3 cli.py oauth --profile {profile_name}'"
        )
        
    return creds

# Helper: Check if credential contains the required scope
def check_creds_scope(creds: Credentials, required_scope: str, profile_name: str) -> Optional[str]:
    scopes = getattr(creds, "scopes", None) or []
    if required_scope not in scopes:
        return (
            f"Error: Profile '{profile_name}' is not authorized for scope '{required_scope}'. "
            f"Authorized scopes: {list(scopes)}. "
            f"Please run the Google OAuth flow to grant all required scopes: "
            f"'python3 cli.py oauth --profile {profile_name}'"
        )
    return None

def get_profile_credentials_with_scope(profile_name: str, required_scope: str) -> Credentials:
    creds = get_profile_credentials(profile_name)
    err = check_creds_scope(creds, required_scope, profile_name)
    if err:
        raise ValueError(err)
    return creds

# Helper: Get Maps API Key
def get_maps_api_key(profile_name: str) -> Optional[str]:
    profile_dir = os.path.join("profiles", profile_name)
    env_path = os.path.join(profile_dir, ".env")
    if os.path.exists(env_path):
        env_data = dotenv_values(env_path)
        key = env_data.get("GOOGLE_MAPS_API_KEY")
        if key:
            return key
    return os.environ.get("GOOGLE_MAPS_API_KEY")

# Helper: Get cache directory for a profile
def get_profile_cache_dir(profile_name: str) -> str:
    cache_dir = os.path.join("profiles", profile_name, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir

# Helper: Extract profile name from MCP Context
async def get_profile_name(ctx: Context) -> str:
    request = ctx.request_context.request
    if not request:
        # Fallback for stdio / non-http calls (e.g. local testing)
        default_profile = os.environ.get("DEFAULT_PROFILE", "default")
        logger.warning(f"No HTTP request context. Falling back to profile '{default_profile}'.")
        return default_profile
        
    session_id = request.query_params.get("session_id")
    if not session_id:
        raise ValueError("Missing 'session_id' in HTTP request query params.")
        
    profile_name = session_to_profile.get(session_id)
    if not profile_name:
        raise ValueError(f"Session '{session_id}' is not mapped to any authenticated profile.")
        
    return profile_name


# ==============================================================================
# Google Drive Tools
# ==============================================================================

@mcp.tool()
async def google_drive_list_files(ctx: Context, query: str = None, page_size: int = 10) -> str:
    """List files in Google Drive.
    
    Args:
        query: Optional search query (e.g. "name contains 'Invoice' and trashed = false").
        page_size: Max number of files to return (default: 10).
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/drive")
        drive_service = build("drive", "v3", credentials=creds)
        
        results = drive_service.files().list(
            q=query, pageSize=page_size, fields="files(id, name, mimeType, size, modifiedTime)"
        ).execute()
        files = results.get('files', [])
        
        if not files:
            return "No files found."
        return json.dumps(files, indent=2)
    except Exception as e:
        logger.exception("Error listing Drive files")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_drive_search_files(ctx: Context, name: str) -> str:
    """Search for files in Google Drive matching a specific name pattern.
    
    Args:
        name: Part of the file name to search for (case-insensitive).
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/drive")
        drive_service = build("drive", "v3", credentials=creds)
        
        # Escape single quotes in name
        safe_name = name.replace("'", "\\'")
        query = f"name contains '{safe_name}' and trashed = false"
        
        results = drive_service.files().list(
            q=query, fields="files(id, name, mimeType, size, modifiedTime)"
        ).execute()
        files = results.get('files', [])
        
        if not files:
            return f"No files found matching name: '{name}'"
        return json.dumps(files, indent=2)
    except Exception as e:
        logger.exception("Error searching Drive files")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_drive_download_file(ctx: Context, file_id: str, use_cache: bool = True) -> str:
    """Download a file from Google Drive, saving it inside the profile's cache folder.
    
    Args:
        file_id: The Google Drive file ID.
        use_cache: If True, uses the cached file if size & MD5 checksum match (default: True).
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/drive")
        drive_service = build("drive", "v3", credentials=creds)
        
        # Retrieve live metadata
        file_meta = drive_service.files().get(
            fileId=file_id, fields="name, size, md5Checksum, mimeType"
        ).execute()
        
        file_name = file_meta.get("name")
        file_size = int(file_meta.get("size", 0))
        md5_checksum = file_meta.get("md5Checksum")
        
        cache_dir = get_profile_cache_dir(profile_name)
        cached_file_path = os.path.join(cache_dir, file_id)
        metadata_path = os.path.join(cache_dir, f"{file_id}.json")
        
        is_cached = False
        if use_cache and os.path.exists(cached_file_path) and os.path.exists(metadata_path):
            try:
                with open(metadata_path, "r") as f:
                    meta = json.load(f)
                if (meta.get("size") == file_size and 
                    meta.get("md5Checksum") == md5_checksum and 
                    meta.get("name") == file_name):
                    is_cached = True
            except Exception:
                pass
                
        if is_cached:
            return json.dumps({
                "status": "success",
                "message": "File retrieved from cache.",
                "file_name": file_name,
                "local_path": cached_file_path,
                "size_bytes": file_size,
                "cached": True
            }, indent=2)
            
        # Download from API
        from googleapiclient.http import MediaIoBaseDownload
        import io
        
        logger.info(f"Downloading file ID {file_id} from Drive for profile '{profile_name}'")
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
            
        # Write to cache
        with open(cached_file_path, "wb") as f:
            f.write(fh.getvalue())
            
        # Write metadata
        with open(metadata_path, "w") as f:
            json.dump({
                "name": file_name,
                "size": file_size,
                "md5Checksum": md5_checksum,
                "mimeType": file_meta.get("mimeType")
            }, f)
            
        return json.dumps({
            "status": "success",
            "message": "File downloaded successfully.",
            "file_name": file_name,
            "local_path": cached_file_path,
            "size_bytes": file_size,
            "cached": False
        }, indent=2)
    except Exception as e:
        logger.exception("Error downloading file")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_drive_upload_file(ctx: Context, local_file_path: str, name: str = None, parent_id: str = None) -> str:
    """Upload a local file to Google Drive.
    
    Args:
        local_file_path: The local filesystem path of the file to upload.
        name: Name to give the file in Drive (optional, defaults to local filename).
        parent_id: Optional ID of the parent folder in Drive to upload into.
    """
    try:
        profile_name = await get_profile_name(ctx)
        
        # Verify local file exists
        if not os.path.exists(local_file_path):
            # Try to resolve relative to profile directory as a security fallback
            profile_dir = os.path.join("profiles", profile_name)
            fallback_path = os.path.join(profile_dir, local_file_path)
            if os.path.exists(fallback_path):
                local_file_path = fallback_path
            else:
                return f"Error: Local file '{local_file_path}' does not exist."
                
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/drive")
        drive_service = build("drive", "v3", credentials=creds)
        
        from googleapiclient.http import MediaFileUpload
        
        file_name = name or os.path.basename(local_file_path)
        file_metadata = {'name': file_name}
        if parent_id:
            file_metadata['parents'] = [parent_id]
            
        media = MediaFileUpload(local_file_path, resumable=True)
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, name').execute()
        
        return json.dumps({
            "status": "success",
            "message": "File uploaded successfully.",
            "file_id": file.get("id"),
            "name": file.get("name")
        }, indent=2)
    except Exception as e:
        logger.exception("Error uploading file")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_drive_create_folder(ctx: Context, name: str, parent_id: str = None) -> str:
    """Create a new folder in Google Drive.
    
    Args:
        name: The name of the new folder.
        parent_id: Optional ID of the parent folder in Drive.
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/drive")
        drive_service = build("drive", "v3", credentials=creds)
        
        file_metadata = {
            'name': name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]
            
        file = drive_service.files().create(body=file_metadata, fields='id, name').execute()
        return json.dumps({
            "status": "success",
            "folder_id": file.get("id"),
            "name": file.get("name")
        }, indent=2)
    except Exception as e:
        logger.exception("Error creating folder")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_drive_copy_file(ctx: Context, file_id: str, name: str = None, parent_id: str = None) -> str:
    """Copy an existing file in Google Drive.
    
    Args:
        file_id: The ID of the file in Google Drive to copy.
        name: Optional new name for the copied file.
        parent_id: Optional ID of the parent folder to place the copy.
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/drive")
        drive_service = build("drive", "v3", credentials=creds)
        
        body = {}
        if name:
            body['name'] = name
        if parent_id:
            body['parents'] = [parent_id]
            
        file = drive_service.files().copy(fileId=file_id, body=body, fields='id, name').execute()
        return json.dumps({
            "status": "success",
            "file_id": file.get("id"),
            "name": file.get("name")
        }, indent=2)
    except Exception as e:
        logger.exception("Error copying file")
        return f"Error: {str(e)}"


# ==============================================================================
# Google Docs Tools
# ==============================================================================

@mcp.tool()
async def google_docs_get_document(ctx: Context, document_id: str) -> str:
    """Retrieve the plain text content of a Google Doc.
    
    Args:
        document_id: The document ID.
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/documents")
        docs_service = build("docs", "v1", credentials=creds)
        
        doc = docs_service.documents().get(documentId=document_id).execute()
        
        def read_structural_elements(elements):
            text = ""
            for value in elements:
                if 'paragraph' in value:
                    elements = value.get('paragraph').get('elements')
                    for elem in elements:
                        if 'textRun' in elem:
                            text += elem.get('textRun').get('content')
                elif 'table' in value:
                    table = value.get('table')
                    for row in table.get('tableRows'):
                        for cell in row.get('tableCells'):
                            text += read_structural_elements(cell.get('content'))
                elif 'tableOfContents' in value:
                    toc = value.get('tableOfContents')
                    text += read_structural_elements(toc.get('content'))
            return text
            
        text = read_structural_elements(doc.get('body').get('content'))
        return json.dumps({
            "title": doc.get("title"),
            "document_id": document_id,
            "content": text
        }, indent=2)
    except Exception as e:
        logger.exception("Error retrieving document")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_docs_create_document(ctx: Context, title: str) -> str:
    """Create a new Google Document.
    
    Args:
        title: The title of the document.
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/documents")
        docs_service = build("docs", "v1", credentials=creds)
        
        doc = docs_service.documents().create(body={'title': title}).execute()
        return json.dumps({
            "status": "success",
            "document_id": doc.get("documentId"),
            "title": doc.get("title")
        }, indent=2)
    except Exception as e:
        logger.exception("Error creating document")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_docs_append_text(ctx: Context, document_id: str, text: str) -> str:
    """Append text to the end of a Google Document.
    
    Args:
        document_id: The document ID.
        text: The string to append.
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/documents")
        docs_service = build("docs", "v1", credentials=creds)
        
        # Get current document structure to calculate endIndex
        doc = docs_service.documents().get(documentId=document_id).execute()
        body = doc.get('body').get('content')
        end_index = body[-1].get('endIndex') - 1 if body else 1
        
        requests = [{
            'insertText': {
                'location': {'index': end_index},
                'text': text
            }
        }]
        docs_service.documents().batchUpdate(documentId=document_id, body={'requests': requests}).execute()
        return f"Successfully appended text to document {document_id}"
    except Exception as e:
        logger.exception("Error appending text to document")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_docs_format_text(ctx: Context, document_id: str, text_to_format: str, bold: bool = None, italic: bool = None, underline: bool = None, font_size: float = None) -> str:
    """Format specific text occurrences within a Google Document (bold, italic, underline, size).
    
    Args:
        document_id: The Google Document ID.
        text_to_format: The specific string to format inside the doc.
        bold: Optional boolean to set text bold.
        italic: Optional boolean to set text italic.
        underline: Optional boolean to set text underline.
        font_size: Optional font size in points (e.g. 12.0).
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/documents")
        docs_service = build("docs", "v1", credentials=creds)
        
        doc = docs_service.documents().get(documentId=document_id).execute()
        
        def find_text_ranges(elements, target_text):
            ranges = []
            for value in elements:
                if 'paragraph' in value:
                    paras = value.get('paragraph').get('elements')
                    for elem in paras:
                        if 'textRun' in elem:
                            run = elem.get('textRun')
                            content = run.get('content')
                            if target_text in content:
                                start = elem.get('startIndex') + content.index(target_text)
                                end = start + len(target_text)
                                ranges.append({'startIndex': start, 'endIndex': end})
                elif 'table' in value:
                    table = value.get('table')
                    for row in table.get('tableRows'):
                        for cell in row.get('tableCells'):
                            ranges.extend(find_text_ranges(cell.get('content'), target_text))
            return ranges
            
        ranges = find_text_ranges(doc.get('body').get('content'), text_to_format)
        if not ranges:
            return f"Error: Text '{text_to_format}' not found in document."
            
        requests = []
        for r in ranges:
            fields = []
            text_style = {}
            if bold is not None:
                text_style['bold'] = bold
                fields.append('bold')
            if italic is not None:
                text_style['italic'] = italic
                fields.append('italic')
            if underline is not None:
                text_style['underline'] = underline
                fields.append('underline')
            if font_size is not None:
                text_style['fontSize'] = {'magnitude': font_size, 'unit': 'PT'}
                fields.append('fontSize')
                
            if fields:
                requests.append({
                    'updateTextStyle': {
                        'range': r,
                        'textStyle': text_style,
                        'fields': ','.join(fields)
                    }
                })
                
        if not requests:
            return "Error: No formatting parameters provided."
            
        docs_service.documents().batchUpdate(documentId=document_id, body={'requests': requests}).execute()
        return f"Successfully formatted {len(ranges)} occurrences of '{text_to_format}' in document {document_id}"
    except Exception as e:
        logger.exception("Error formatting text")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_docs_render_to_markdown(ctx: Context, document_id: str) -> str:
    """Read a Google Document and render its structural content to Markdown format.
    
    Args:
        document_id: The document ID.
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/documents")
        docs_service = build("docs", "v1", credentials=creds)
        
        doc = docs_service.documents().get(documentId=document_id).execute()
        
        def doc_body_to_markdown(elements_container):
            markdown = ""
            elements = elements_container.get('content') if isinstance(elements_container, dict) else elements_container
            if not elements:
                return ""
                
            for elem in elements:
                if 'paragraph' in elem:
                    para = elem.get('paragraph')
                    para_style = para.get('paragraphStyle', {})
                    style_name = para_style.get('namedStyleType', 'NORMAL_TEXT')
                    
                    bullet = para.get('bullet')
                    prefix = ""
                    if bullet:
                        prefix = "* "
                        
                    para_text = ""
                    for run_elem in para.get('elements', []):
                        if 'textRun' in run_elem:
                            run = run_elem.get('textRun')
                            text = run.get('content', '')
                            style = run.get('textStyle', {})
                            
                            if style.get('bold'):
                                text = f"**{text.strip()}**" + (" " if text.endswith(" ") else "")
                            if style.get('italic'):
                                text = f"*{text.strip()}*" + (" " if text.endswith(" ") else "")
                            para_text += text
                            
                    if not para_text.strip():
                        continue
                        
                    if style_name == 'HEADING_1':
                        markdown += f"# {para_text.strip()}\n\n"
                    elif style_name == 'HEADING_2':
                        markdown += f"## {para_text.strip()}\n\n"
                    elif style_name == 'HEADING_3':
                        markdown += f"### {para_text.strip()}\n\n"
                    else:
                        markdown += f"{prefix}{para_text.strip()}\n\n"
                        
                elif 'table' in elem:
                    table = elem.get('table')
                    for row in table.get('tableRows', []):
                        row_cells = []
                        for cell in row.get('tableCells', []):
                            cell_md = doc_body_to_markdown(cell.get('content')).strip()
                            row_cells.append(cell_md)
                        markdown += "| " + " | ".join(row_cells) + " |\n"
                    markdown += "\n"
            return markdown
            
        md_text = doc_body_to_markdown(doc.get('body'))
        return json.dumps({
            "title": doc.get("title"),
            "document_id": document_id,
            "markdown": md_text
        }, indent=2)
    except Exception as e:
        logger.exception("Error rendering doc to markdown")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_docs_create_from_markdown(ctx: Context, title: str, markdown: str) -> str:
    """Create a new Google Document, rendering Markdown formatting (headings, lists, bold, italics) to Doc elements.
    
    Args:
        title: The title of the new document.
        markdown: The Markdown formatted string content.
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/documents")
        docs_service = build("docs", "v1", credentials=creds)
        
        # 1. Create empty document
        doc = docs_service.documents().create(body={'title': title}).execute()
        document_id = doc.get("documentId")
        
        import re
        requests = []
        lines = markdown.split('\n')
        current_index = 1
        
        for line in lines:
            stripped = line.strip()
            if not stripped:
                requests.append({
                    'insertText': {
                        'location': {'index': current_index},
                        'text': "\n"
                    }
                })
                current_index += 1
                continue
                
            style_name = 'NORMAL_TEXT'
            text_content = stripped
            if stripped.startswith('# '):
                style_name = 'HEADING_1'
                text_content = stripped[2:]
            elif stripped.startswith('## '):
                style_name = 'HEADING_2'
                text_content = stripped[3:]
            elif stripped.startswith('### '):
                style_name = 'HEADING_3'
                text_content = stripped[4:]
            elif stripped.startswith('* ') or stripped.startswith('- '):
                text_content = "• " + stripped[2:]
                
            plain_text = text_content
            bold_ranges = []
            italic_ranges = []
            
            while True:
                match = re.search(r'\*\*(.*?)\*\*', plain_text)
                if not match:
                    break
                start = match.start()
                content = match.group(1)
                plain_text = plain_text[:start] + content + plain_text[match.end():]
                bold_ranges.append((start, start + len(content)))
                
            while True:
                match = re.search(r'\*(.*?)\*', plain_text)
                if not match:
                    break
                start = match.start()
                content = match.group(1)
                plain_text = plain_text[:start] + content + plain_text[match.end():]
                italic_ranges.append((start, start + len(content)))
                
            plain_text += "\n"
            length = len(plain_text)
            
            # Insert text content
            requests.append({
                'insertText': {
                    'location': {'index': current_index},
                    'text': plain_text
                }
            })
            
            # Apply structural paragraph style
            requests.append({
                'updateParagraphStyle': {
                    'range': {
                        'startIndex': current_index,
                        'endIndex': current_index + length
                    },
                    'paragraphStyle': {
                        'namedStyleType': style_name
                    },
                    'fields': 'namedStyleType'
                }
            })
            
            # Apply inline bold style
            for start, end in bold_ranges:
                requests.append({
                    'updateTextStyle': {
                        'range': {
                            'startIndex': current_index + start,
                            'endIndex': current_index + end
                        },
                        'textStyle': {'bold': True},
                        'fields': 'bold'
                    }
                })
                
            # Apply inline italic style
            for start, end in italic_ranges:
                requests.append({
                    'updateTextStyle': {
                        'range': {
                            'startIndex': current_index + start,
                            'endIndex': current_index + end
                        },
                        'textStyle': {'italic': True},
                        'fields': 'italic'
                    }
                })
                
            current_index += length
            
        if requests:
            docs_service.documents().batchUpdate(documentId=document_id, body={'requests': requests}).execute()
            
        return json.dumps({
            "status": "success",
            "document_id": document_id,
            "title": title,
            "message": "Document created from markdown successfully."
        }, indent=2)
    except Exception as e:
        logger.exception("Error creating document from markdown")
        return f"Error: {str(e)}"


# ==============================================================================
# Google Sheets Tools
# ==============================================================================

@mcp.tool()
async def google_sheets_get_spreadsheet(ctx: Context, spreadsheet_id: str, range_name: str = "Sheet1!A1:Z100") -> str:
    """Retrieve values from a Google Spreadsheet range.
    
    Args:
        spreadsheet_id: The spreadsheet ID.
        range_name: Range in A1 notation (default: "Sheet1!A1:Z100").
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/spreadsheets")
        sheets_service = build("sheets", "v4", credentials=creds)
        
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=range_name
        ).execute()
        values = result.get('values', [])
        
        return json.dumps({
            "spreadsheet_id": spreadsheet_id,
            "range": range_name,
            "values": values
        }, indent=2)
    except Exception as e:
        logger.exception("Error retrieving spreadsheet values")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_sheets_update_spreadsheet(ctx: Context, spreadsheet_id: str, range_name: str, values: list[list[str]]) -> str:
    """Update values in a Google Spreadsheet range.
    
    Args:
        spreadsheet_id: The spreadsheet ID.
        range_name: Range in A1 notation.
        values: Two-dimensional array of string values.
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/spreadsheets")
        sheets_service = build("sheets", "v4", credentials=creds)
        
        result = sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body={"values": values}
        ).execute()
        
        return json.dumps({
            "status": "success",
            "updatedCells": result.get("updatedCells"),
            "updatedRange": result.get("updatedRange")
        }, indent=2)
    except Exception as e:
        logger.exception("Error updating spreadsheet values")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_sheets_create_spreadsheet(ctx: Context, title: str) -> str:
    """Create a new Google Spreadsheet.
    
    Args:
        title: Title of the new spreadsheet.
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/spreadsheets")
        sheets_service = build("sheets", "v4", credentials=creds)
        
        spreadsheet = sheets_service.spreadsheets().create(
            body={"properties": {"title": title}}
        ).execute()
        
        return json.dumps({
            "status": "success",
            "spreadsheet_id": spreadsheet.get("spreadsheetId"),
            "spreadsheet_url": spreadsheet.get("spreadsheetUrl")
        }, indent=2)
    except Exception as e:
        logger.exception("Error creating spreadsheet")
        return f"Error: {str(e)}"


# ==============================================================================
# Google Tasks Tools
# ==============================================================================

@mcp.tool()
async def google_tasks_list_task_lists(ctx: Context) -> str:
    """List all the user's task lists."""
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/tasks")
        tasks_service = build("tasks", "v1", credentials=creds)
        
        results = tasks_service.tasklists().list().execute()
        items = results.get('items', [])
        return json.dumps(items, indent=2)
    except Exception as e:
        logger.exception("Error listing task lists")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_tasks_list_tasks(ctx: Context, tasklist_id: str = "@default") -> str:
    """List tasks in a specific task list.
    
    Args:
        tasklist_id: Task list ID (default is '@default').
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/tasks")
        tasks_service = build("tasks", "v1", credentials=creds)
        
        results = tasks_service.tasks().list(tasklist=tasklist_id).execute()
        items = results.get('items', [])
        return json.dumps(items, indent=2)
    except Exception as e:
        logger.exception("Error listing tasks")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_tasks_create_task(ctx: Context, title: str, tasklist_id: str = "@default", notes: str = None, due: str = None) -> str:
    """Create a new task.
    
    Args:
        title: Title of the task.
        tasklist_id: Task list ID (default is '@default').
        notes: Optional description/notes.
        due: Optional due date in RFC 3339 format (e.g. '2026-06-03T12:00:00Z').
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/tasks")
        tasks_service = build("tasks", "v1", credentials=creds)
        
        body = {'title': title}
        if notes:
            body['notes'] = notes
        if due:
            body['due'] = due
            
        task = tasks_service.tasks().insert(tasklist=tasklist_id, body=body).execute()
        return json.dumps({
            "status": "success",
            "task_id": task.get("id"),
            "title": task.get("title")
        }, indent=2)
    except Exception as e:
        logger.exception("Error creating task")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_tasks_complete_task(ctx: Context, tasklist_id: str, task_id: str) -> str:
    """Mark a task as completed.
    
    Args:
        tasklist_id: The ID of the task list containing the task.
        task_id: The ID of the task to complete.
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/tasks")
        tasks_service = build("tasks", "v1", credentials=creds)
        
        task = tasks_service.tasks().patch(
            tasklist=tasklist_id, task=task_id, body={'status': 'completed'}
        ).execute()
        
        return f"Successfully marked task '{task.get('title')}' as completed."
    except Exception as e:
        logger.exception("Error completing task")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_tasks_create_task_list(ctx: Context, title: str) -> str:
    """Create a new Google Task list.
    
    Args:
        title: The title of the new task list.
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/tasks")
        tasks_service = build("tasks", "v1", credentials=creds)
        
        body = {'title': title}
        result = tasks_service.tasklists().insert(body=body).execute()
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.exception("Error creating task list")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_tasks_delete_task_list(ctx: Context, tasklist_id: str) -> str:
    """Delete a Google Task list.
    
    Args:
        tasklist_id: The ID of the task list to delete.
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/tasks")
        tasks_service = build("tasks", "v1", credentials=creds)
        
        tasks_service.tasklists().delete(tasklist=tasklist_id).execute()
        return f"Successfully deleted task list '{tasklist_id}'."
    except Exception as e:
        logger.exception("Error deleting task list")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_tasks_update_task(ctx: Context, tasklist_id: str, task_id: str, title: str = None, notes: str = None, due: str = None) -> str:
    """Update details of an existing Google Task.
    
    Args:
        tasklist_id: The ID of the task list containing the task.
        task_id: The ID of the task to update.
        title: Optional new title for the task.
        notes: Optional new description/notes for the task.
        due: Optional new due date in RFC 3339 format (e.g. '2026-06-03T12:00:00Z').
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/tasks")
        tasks_service = build("tasks", "v1", credentials=creds)
        
        body = {}
        if title is not None:
            body['title'] = title
        if notes is not None:
            body['notes'] = notes
        if due is not None:
            body['due'] = due
            
        if not body:
            return "Error: No update parameters provided (title, notes, or due)."
            
        result = tasks_service.tasks().patch(tasklist=tasklist_id, task=task_id, body=body).execute()
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.exception("Error updating task")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_tasks_delete_task(ctx: Context, tasklist_id: str, task_id: str) -> str:
    """Delete a Google Task.
    
    Args:
        tasklist_id: The ID of the task list containing the task.
        task_id: The ID of the task to delete.
    """
    try:
        profile_name = await get_profile_name(ctx)
        creds = get_profile_credentials_with_scope(profile_name, "https://www.googleapis.com/auth/tasks")
        tasks_service = build("tasks", "v1", credentials=creds)
        
        tasks_service.tasks().delete(tasklist=tasklist_id, task=task_id).execute()
        return f"Successfully deleted task '{task_id}' from list '{tasklist_id}'."
    except Exception as e:
        logger.exception("Error deleting task")
        return f"Error: {str(e)}"


# ==============================================================================
# Google Maps Tools
# ==============================================================================

@mcp.tool()
async def google_maps_search(ctx: Context, query: str) -> str:
    """Search for places/businesses/coordinates on Google Maps.
    
    Args:
        query: Search query (e.g. 'coffee shop near Capitol Hill').
    """
    try:
        profile_name = await get_profile_name(ctx)
        api_key = get_maps_api_key(profile_name)
        if not api_key:
            return "Error: GOOGLE_MAPS_API_KEY is not configured for this profile. Please add it to your profile's .env file."
            
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params={"query": query, "key": api_key}
            )
            data = resp.json()
            
        if data.get("status") != "OK":
            return f"Maps Search Error: {data.get('status')} - {data.get('error_message', 'No details available')}"
            
        results = data.get("results", [])
        return json.dumps(results[:10], indent=2)
    except Exception as e:
        logger.exception("Error searching Google Maps")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_maps_directions(ctx: Context, origin: str, destination: str, mode: str = "driving") -> str:
    """Get navigation directions between two locations.
    
    Args:
        origin: Start location address or latitude/longitude.
        destination: End location address or latitude/longitude.
        mode: Transit mode: 'driving', 'walking', 'bicycling', 'transit' (default: 'driving').
    """
    try:
        profile_name = await get_profile_name(ctx)
        api_key = get_maps_api_key(profile_name)
        if not api_key:
            return "Error: GOOGLE_MAPS_API_KEY is not configured for this profile. Please add it to your profile's .env file."
            
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/directions/json",
                params={
                    "origin": origin,
                    "destination": destination,
                    "mode": mode,
                    "key": api_key
                }
            )
            data = resp.json()
            
        if data.get("status") != "OK":
            return f"Directions Error: {data.get('status')} - {data.get('error_message', 'No details available')}"
            
        routes = data.get("routes", [])
        return json.dumps(routes[:2], indent=2)
    except Exception as e:
        logger.exception("Error getting directions")
        return f"Error: {str(e)}"


# ==============================================================================
# Google News Tools
# ==============================================================================

@mcp.tool()
async def google_news_top_news(ctx: Context, query: str = None) -> str:
    """Fetch top news or search news stories from Google News RSS.
    
    Args:
        query: Optional search keyword to filter news.
    """
    try:
        if query:
            url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"
        else:
            url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
            
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        # Execute XML fetch synchronously in thread pool
        import anyio
        def fetch():
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.read()
                
        xml_data = await anyio.to_thread.run_sync(fetch)
        root = ET.fromstring(xml_data)
        
        items = []
        for item in root.findall('.//item')[:15]:
            title = item.find('title').text if item.find('title') is not None else ""
            link = item.find('link').text if item.find('link') is not None else ""
            pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
            source = item.find('source').text if item.find('source') is not None else ""
            items.append({
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "source": source
            })
            
        return json.dumps(items, indent=2)
    except Exception as e:
        logger.exception("Error retrieving news")
        return f"Error: {str(e)}"

@mcp.tool()
async def google_news_get_article_text(ctx: Context, url: str) -> str:
    """Extract and read the full text content of a news article from its URL.
    
    Args:
        url: The URL of the news article.
    """
    try:
        from bs4 import BeautifulSoup
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5"
        }
        
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers, timeout=15)
            
        if resp.status_code != 200:
            return f"Error: Failed to fetch article HTML. HTTP Status Code: {resp.status_code}"
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Decompose non-article layout elements
        for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
            element.decompose()
            
        paragraphs = soup.find_all('p')
        text_blocks = []
        for p in paragraphs:
            txt = p.get_text().strip()
            if txt and len(txt) > 25:
                text_blocks.append(txt)
                
        if not text_blocks:
            # Fallback: search main tags
            main_content = soup.find('article') or soup.find('main')
            if main_content:
                txt = main_content.get_text(separator="\n\n").strip()
                cleaned_lines = [line.strip() for line in txt.split("\n") if line.strip()]
                return "\n\n".join(cleaned_lines[:50])
            return "Error: Could not automatically locate article body text on this page."
            
        full_text = "\n\n".join(text_blocks)
        return full_text[:12000] # Limit to 12k characters
    except Exception as e:
        logger.exception("Error extracting article text")
        return f"Error: {str(e)}"


# ==============================================================================
# Starlette / SSE Server Configuration
# ==============================================================================

# Normalizing message endpoint
sse_transport = SseServerTransport("/messages/")

async def handle_sse(request: Request):
    # Retrieve the token
    token = request.query_params.get("token")
    if not token:
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:]
            
    if not token:
        logger.warning("Rejecting connection request: Missing token")
        return Response("Unauthorized: Missing token", status_code=401)
        
    profile_name = get_profile_by_token(token)
    if not profile_name:
        logger.warning(f"Rejecting connection request: Invalid token '{token}'")
        return Response("Unauthorized: Invalid token", status_code=401)
        
    logger.info(f"Token matches profile '{profile_name}'. Establishing SSE connection...")
    
    existing_keys = set(sse_transport._read_stream_writers.keys())
    
    async with sse_transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
        new_keys = set(sse_transport._read_stream_writers.keys()) - existing_keys
        session_id = None
        if new_keys:
            session_id = list(new_keys)[0].hex
            session_to_profile[session_id] = profile_name
            logger.info(f"Associated Session ID '{session_id}' with profile '{profile_name}'")
            
        try:
            # Run low-level MCP server loops
            await mcp._mcp_server.run(
                read_stream,
                write_stream,
                mcp._mcp_server.create_initialization_options()
            )
        finally:
            if session_id:
                session_to_profile.pop(session_id, None)
                logger.info(f"Cleaned up session ID '{session_id}' from profile mappings.")
                
    return Response()

routes = [
    Route("/sse", endpoint=handle_sse, methods=["GET"]),
    Mount("/messages", app=sse_transport.handle_post_message),
]

app = Starlette(routes=routes)

def run():
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    logger.info(f"Starting MCP SSE Server on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    run()
