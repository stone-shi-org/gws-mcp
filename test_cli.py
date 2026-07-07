#!/usr/bin/env python3
import os
import sys
import json
import tempfile
import shutil
import pytest
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cli


class TestLoadProfileEnv:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.test_dir)

    def test_returns_empty_dict_when_env_missing(self):
        result = cli.load_profile_env(self.test_dir)
        assert result == {}

    def test_loads_env_values(self):
        env_path = os.path.join(self.test_dir, ".env")
        with open(env_path, "w") as f:
            f.write("PROFILE_TOKEN=abc123\n")
            f.write("CALENDAR_ACCOUNTS=[]\n")
        result = cli.load_profile_env(self.test_dir)
        assert result["PROFILE_TOKEN"] == "abc123"
        assert result["CALENDAR_ACCOUNTS"] == "[]"


class TestGetGooglePathsFromEnv:
    def test_returns_default_paths_when_no_accounts(self):
        env_data = {}
        creds_path, token_path = cli.get_google_paths_from_env("/profile", env_data)
        assert creds_path == "/profile/google_cli_client.json"
        assert token_path == "/profile/google_calendar_token.json"

    def test_returns_default_paths_when_accounts_empty(self):
        env_data = {"CALENDAR_ACCOUNTS": "[]"}
        creds_path, token_path = cli.get_google_paths_from_env("/profile", env_data)
        assert creds_path == "/profile/google_cli_client.json"
        assert token_path == "/profile/google_calendar_token.json"

    def test_extracts_paths_from_google_account(self):
        accounts = [{"type": "google", "credentials_path": "custom_creds.json", "token_path": "custom_token.json"}]
        env_data = {"CALENDAR_ACCOUNTS": json.dumps(accounts)}
        creds_path, token_path = cli.get_google_paths_from_env("/profile", env_data)
        assert creds_path == "/profile/custom_creds.json"
        assert token_path == "/profile/custom_token.json"

    def test_handles_absolute_paths(self):
        accounts = [{"type": "google", "credentials_path": "/absolute/creds.json", "token_path": "/absolute/token.json"}]
        env_data = {"CALENDAR_ACCOUNTS": json.dumps(accounts)}
        creds_path, token_path = cli.get_google_paths_from_env("/profile", env_data)
        assert creds_path == "/absolute/creds.json"
        assert token_path == "/absolute/token.json"

    def test_handles_invalid_json_gracefully(self):
        env_data = {"CALENDAR_ACCOUNTS": "invalid json"}
        creds_path, token_path = cli.get_google_paths_from_env("/profile", env_data)
        assert creds_path == "/profile/google_cli_client.json"
        assert token_path == "/profile/google_calendar_token.json"

    def test_skips_non_google_accounts(self):
        accounts = [{"type": "outlook", "credentials_path": "outlook_creds.json"}]
        env_data = {"CALENDAR_ACCOUNTS": json.dumps(accounts)}
        creds_path, token_path = cli.get_google_paths_from_env("/profile", env_data)
        assert creds_path == "/profile/google_cli_client.json"
        assert token_path == "/profile/google_calendar_token.json"


class TestGenerateToken:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)

    def teardown_method(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)

    def test_creates_profile_directory(self):
        cli.generate_token("new_profile")
        assert os.path.exists(os.path.join("profiles", "new_profile"))

    def test_creates_env_file_with_token(self):
        token = cli.generate_token("test_profile")
        env_path = os.path.join("profiles", "test_profile", ".env")
        assert os.path.exists(env_path)
        with open(env_path) as f:
            content = f.read()
        assert f"PROFILE_TOKEN={token}" in content

    def test_updates_existing_env_file(self):
        profile_dir = os.path.join("profiles", "existing_profile")
        os.makedirs(profile_dir)
        env_path = os.path.join(profile_dir, ".env")
        with open(env_path, "w") as f:
            f.write("OTHER_VAR=value\n")
            f.write("PROFILE_TOKEN=old_token\n")

        new_token = cli.generate_token("existing_profile")

        with open(env_path) as f:
            content = f.read()
        assert f"PROFILE_TOKEN={new_token}" in content
        assert "old_token" not in content
        assert "OTHER_VAR=value" in content

    def test_token_is_hex_string(self):
        token = cli.generate_token("test_profile")
        assert len(token) == 32
        assert all(c in "0123456789abcdef" for c in token)


class TestMain:
    def test_parser_requires_command(self):
        with patch("sys.argv", ["cli.py"]):
            with pytest.raises(SystemExit):
                cli.main()

    @patch("cli.generate_token")
    def test_generate_token_command(self, mock_generate):
        mock_generate.return_value = "test_token"
        with patch("sys.argv", ["cli.py", "generate-token", "--profile", "test"]):
            cli.main()
        mock_generate.assert_called_once_with("test")

    @patch("cli.run_oauth")
    def test_oauth_command(self, mock_oauth):
        with patch("sys.argv", ["cli.py", "oauth", "--profile", "test"]):
            cli.main()
        mock_oauth.assert_called_once_with("test", headless=False, port=8080)

    @patch("cli.run_oauth")
    def test_oauth_headless_command(self, mock_oauth):
        with patch("sys.argv", ["cli.py", "oauth", "--profile", "test", "--headless", "--port", "9090"]):
            cli.main()
        mock_oauth.assert_called_once_with("test", headless=True, port=9090)
