#!/usr/bin/env python3
import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
import sys
import json
import secrets
import argparse
from dotenv import dotenv_values

# Google Auth Libraries
try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Error: google-auth-oauthlib is not installed. Please install it with 'pip install google-auth-oauthlib'")
    sys.exit(1)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar",
]

def load_profile_env(profile_dir):
    env_path = os.path.join(profile_dir, ".env")
    if os.path.exists(env_path):
        return dotenv_values(env_path)
    return {}

def get_google_paths_from_env(profile_dir, env_data):
    # Try to parse CALENDAR_ACCOUNTS to find credentials and token paths
    accounts_str = env_data.get("CALENDAR_ACCOUNTS")
    creds_path = "google_cli_client.json"
    token_path = "google_calendar_token.json"
    
    if accounts_str:
        try:
            accounts = json.loads(accounts_str)
            for acc in accounts:
                if acc.get("type") == "google":
                    creds_path = acc.get("credentials_path", creds_path)
                    token_path = acc.get("token_path", token_path)
                    break
        except Exception as e:
            print(f"Warning: Failed to parse CALENDAR_ACCOUNTS JSON: {e}")
            
    # Resolve relative paths relative to profile directory
    creds_full = os.path.join(profile_dir, creds_path) if not os.path.isabs(creds_path) else creds_path
    token_full = os.path.join(profile_dir, token_path) if not os.path.isabs(token_path) else token_path
    
    return creds_full, token_full

def generate_token(profile_name):
    profiles_dir = "profiles"
    profile_dir = os.path.join(profiles_dir, profile_name)
    os.makedirs(profile_dir, exist_ok=True)
    
    token = secrets.token_hex(16)
    env_path = os.path.join(profile_dir, ".env")
    
    # Read existing content if any
    lines = []
    token_updated = False
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.strip().startswith("PROFILE_TOKEN=") or line.strip().startswith("CALENDAR_PROFILE_TOKEN="):
                    lines.append(f"PROFILE_TOKEN={token}\n")
                    token_updated = True
                else:
                    lines.append(line)
                    
    if not token_updated:
        # If file doesn't end with newline, append one
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"PROFILE_TOKEN={token}\n")
        
    with open(env_path, "w") as f:
        f.writelines(lines)
        
    print(f"Generated new token for profile '{profile_name}': {token}")
    print(f"Saved token to: {env_path}")
    return token

def run_oauth(profile_name, headless=False, port=8080):
    # Allow insecure transport (http redirect URI) for local OAuth flows
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    
    profiles_dir = "profiles"
    profile_dir = os.path.join(profiles_dir, profile_name)
    if not os.path.exists(profile_dir):
        print(f"Error: Profile folder '{profile_dir}' does not exist. Run 'generate-token' first.")
        sys.exit(1)
        
    env_data = load_profile_env(profile_dir)
    creds_full, token_full = get_google_paths_from_env(profile_dir, env_data)
    
    if not os.path.exists(creds_full):
        print(f"Error: Google client secrets file not found at: {creds_full}")
        print("Please place your 'google_cli_client.json' file in the profile directory.")
        sys.exit(1)
        
    print(f"Using Client Secrets: {creds_full}")
    print(f"Saving Token to:      {token_full}")
    
    if headless:
        # Construct flow with a localhost redirect URI
        redirect_uri = f"http://localhost:{port}"
        flow = InstalledAppFlow.from_client_secrets_file(
            creds_full,
            scopes=SCOPES,
            redirect_uri=redirect_uri
        )
        
        # Get authorization URL
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        print("\n" + "="*80)
        print("HEADLESS OAUTH FLOW:")
        print("1. Open the following URL in your local browser and complete authorization:")
        print(f"\n{auth_url}\n")
        print("2. After authorizing, your browser will redirect to a page that may fail to load.")
        print(f"   Copy the FULL redirect URL from your browser's address bar (should start with http://localhost:{port}...)")
        print("="*80 + "\n")
        
        try:
            redirect_response = input("Paste the FULL redirect URL: ").strip()
            flow.fetch_token(authorization_response=redirect_response)
        except Exception as e:
            print(f"\nError exchanging authorization code: {e}")
            sys.exit(1)
    else:
        # Run local redirect server
        flow = InstalledAppFlow.from_client_secrets_file(creds_full, scopes=SCOPES)
        try:
            flow.run_local_server(port=port, prompt="consent")
        except Exception as e:
            print(f"\nError running local redirect server: {e}")
            sys.exit(1)
            
    # Save credentials
    credentials = flow.credentials
    os.makedirs(os.path.dirname(token_full), exist_ok=True)
    with open(token_full, "w") as f:
        f.write(credentials.to_json())
        
    print(f"\nSuccess! OAuth credentials saved to {token_full}")

def main():
    parser = argparse.ArgumentParser(description="Google Workspace MCP Profile Utility")
    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcommands")
    
    # generate-token
    parser_token = subparsers.add_parser("generate-token", help="Generate a profile token and save to .env")
    parser_token.add_argument("--profile", required=True, help="Profile name (e.g. stone, work)")
    
    # oauth
    parser_oauth = subparsers.add_parser("oauth", help="Trigger Google OAuth 2.0 authorization")
    parser_oauth.add_argument("--profile", required=True, help="Profile name (e.g. stone, work)")
    parser_oauth.add_argument("--headless", action="store_true", help="Run in headless mode, printing URL and expecting pasted redirect")
    parser_oauth.add_argument("--port", type=int, default=8080, help="Port to use for local redirect / redirect URI (default: 8080)")
    
    args = parser.parse_args()
    
    if args.command == "generate-token":
        generate_token(args.profile)
    elif args.command == "oauth":
        run_oauth(args.profile, headless=args.headless, port=args.port)

if __name__ == "__main__":
    main()
