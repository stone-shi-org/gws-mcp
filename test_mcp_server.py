#!/usr/bin/env python3
import os
import sys
import json
import tempfile
import shutil
import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mcp_server


class TestGetProfileByToken:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)
        os.makedirs("profiles", exist_ok=True)

    def teardown_method(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)

    def test_returns_none_when_profiles_dir_missing(self):
        shutil.rmtree("profiles")
        assert mcp_server.get_profile_by_token("any_token") is None

    def test_returns_none_when_no_profiles_exist(self):
        assert mcp_server.get_profile_by_token("any_token") is None

    def test_finds_profile_by_profile_token(self):
        profile_dir = os.path.join("profiles", "test_profile")
        os.makedirs(profile_dir)
        with open(os.path.join(profile_dir, ".env"), "w") as f:
            f.write("PROFILE_TOKEN=abc123\n")
        assert mcp_server.get_profile_by_token("abc123") == "test_profile"

    def test_finds_profile_by_calendar_profile_token(self):
        profile_dir = os.path.join("profiles", "cal_profile")
        os.makedirs(profile_dir)
        with open(os.path.join(profile_dir, ".env"), "w") as f:
            f.write("CALENDAR_PROFILE_TOKEN=xyz789\n")
        assert mcp_server.get_profile_by_token("xyz789") == "cal_profile"

    def test_returns_none_for_invalid_token(self):
        profile_dir = os.path.join("profiles", "test_profile")
        os.makedirs(profile_dir)
        with open(os.path.join(profile_dir, ".env"), "w") as f:
            f.write("PROFILE_TOKEN=abc123\n")
        assert mcp_server.get_profile_by_token("wrong_token") is None

    def test_skips_directories_without_env(self):
        os.makedirs(os.path.join("profiles", "no_env_profile"))
        assert mcp_server.get_profile_by_token("any_token") is None


class TestGetProfileCredentials:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)

    def teardown_method(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)

    def test_raises_when_token_file_missing(self):
        profile_dir = os.path.join("profiles", "test_profile")
        os.makedirs(profile_dir)
        with open(os.path.join(profile_dir, ".env"), "w") as f:
            f.write("")
        with pytest.raises(ValueError, match="Credentials token file not found"):
            mcp_server.get_profile_credentials("test_profile")

    @patch("mcp_server.Credentials")
    def test_loads_credentials_from_token_file(self, mock_creds_class):
        profile_dir = os.path.join("profiles", "test_profile")
        os.makedirs(profile_dir)
        token_path = os.path.join(profile_dir, "google_calendar_token.json")
        with open(token_path, "w") as f:
            f.write('{"token": "fake_token", "refresh_token": "fake_refresh"}')

        mock_creds = Mock()
        mock_creds.expired = False
        mock_creds_class.from_authorized_user_file.return_value = mock_creds

        result = mcp_server.get_profile_credentials("test_profile")
        assert result == mock_creds
        mock_creds_class.from_authorized_user_file.assert_called_once()

    @patch("mcp_server.Credentials")
    def test_refreshes_expired_credentials(self, mock_creds_class):
        profile_dir = os.path.join("profiles", "test_profile")
        os.makedirs(profile_dir)
        token_path = os.path.join(profile_dir, "google_calendar_token.json")
        creds_path = os.path.join(profile_dir, "google_cli_client.json")

        with open(token_path, "w") as f:
            f.write('{"token": "expired_token", "refresh_token": "fake_refresh"}')
        with open(creds_path, "w") as f:
            json.dump({"installed": {"client_id": "id", "client_secret": "secret"}}, f)

        mock_creds = Mock()
        mock_creds.expired = True
        mock_creds.refresh_token = "fake_refresh"
        mock_creds.to_json.return_value = '{"token": "new_token"}'
        mock_creds_class.from_authorized_user_file.return_value = mock_creds

        with patch("mcp_server.GoogleAuthRequest"):
            result = mcp_server.get_profile_credentials("test_profile")

        mock_creds.refresh.assert_called_once()


class TestCheckCredsScope:
    def test_returns_none_when_scope_present(self):
        creds = Mock()
        creds.scopes = ["https://www.googleapis.com/auth/drive"]
        result = mcp_server.check_creds_scope(creds, "https://www.googleapis.com/auth/drive", "test")
        assert result is None

    def test_returns_error_when_scope_missing(self):
        creds = Mock()
        creds.scopes = ["https://www.googleapis.com/auth/calendar"]
        result = mcp_server.check_creds_scope(creds, "https://www.googleapis.com/auth/drive", "test")
        assert result is not None
        assert "not authorized for scope" in result

    def test_handles_empty_scopes(self):
        creds = Mock()
        creds.scopes = []
        result = mcp_server.check_creds_scope(creds, "https://www.googleapis.com/auth/drive", "test")
        assert result is not None


class TestGetMapsApiKey:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)

    def teardown_method(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)
        if "GOOGLE_MAPS_API_KEY" in os.environ:
            del os.environ["GOOGLE_MAPS_API_KEY"]

    def test_returns_key_from_profile_env(self):
        profile_dir = os.path.join("profiles", "test_profile")
        os.makedirs(profile_dir)
        with open(os.path.join(profile_dir, ".env"), "w") as f:
            f.write("GOOGLE_MAPS_API_KEY=profile_key_123\n")
        assert mcp_server.get_maps_api_key("test_profile") == "profile_key_123"

    def test_returns_key_from_env_when_not_in_profile(self):
        profile_dir = os.path.join("profiles", "test_profile")
        os.makedirs(profile_dir)
        with open(os.path.join(profile_dir, ".env"), "w") as f:
            f.write("")
        os.environ["GOOGLE_MAPS_API_KEY"] = "env_key_456"
        assert mcp_server.get_maps_api_key("test_profile") == "env_key_456"

    def test_returns_none_when_no_key_configured(self):
        profile_dir = os.path.join("profiles", "test_profile")
        os.makedirs(profile_dir)
        with open(os.path.join(profile_dir, ".env"), "w") as f:
            f.write("")
        assert mcp_server.get_maps_api_key("test_profile") is None


class TestGetProfileCacheDir:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)

    def teardown_method(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)

    def test_creates_cache_directory(self):
        profile_dir = os.path.join("profiles", "test_profile")
        os.makedirs(profile_dir)
        cache_dir = mcp_server.get_profile_cache_dir("test_profile")
        assert os.path.exists(cache_dir)
        assert cache_dir.endswith("cache")


class TestGetProfileName:
    @pytest.mark.asyncio
    async def test_returns_default_profile_when_no_request_context(self):
        os.environ["DEFAULT_PROFILE"] = "test_default"
        mock_ctx = Mock()
        mock_ctx.request_context.request = None
        result = await mcp_server.get_profile_name(mock_ctx)
        assert result == "test_default"
        del os.environ["DEFAULT_PROFILE"]

    @pytest.mark.asyncio
    async def test_raises_when_session_id_missing(self):
        mock_ctx = Mock()
        mock_request = Mock()
        mock_request.query_params = {}
        mock_ctx.request_context.request = mock_request
        with pytest.raises(ValueError, match="Missing 'session_id'"):
            await mcp_server.get_profile_name(mock_ctx)

    @pytest.mark.asyncio
    async def test_raises_when_session_not_mapped(self):
        mock_ctx = Mock()
        mock_request = Mock()
        mock_request.query_params = {"session_id": "unknown_session"}
        mock_ctx.request_context.request = mock_request
        mcp_server.session_to_profile.clear()
        with pytest.raises(ValueError, match="not mapped"):
            await mcp_server.get_profile_name(mock_ctx)

    @pytest.mark.asyncio
    async def test_returns_profile_for_mapped_session(self):
        mock_ctx = Mock()
        mock_request = Mock()
        mock_request.query_params = {"session_id": "abc123"}
        mock_ctx.request_context.request = mock_request
        mcp_server.session_to_profile["abc123"] = "my_profile"
        result = await mcp_server.get_profile_name(mock_ctx)
        assert result == "my_profile"
        del mcp_server.session_to_profile["abc123"]


class TestGetProfileCredentialsWithScope:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)

    def teardown_method(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)

    @patch("mcp_server.get_profile_credentials")
    def test_raises_when_scope_missing(self, mock_get_creds):
        mock_creds = Mock()
        mock_creds.scopes = []
        mock_get_creds.return_value = mock_creds
        with pytest.raises(ValueError, match="not authorized"):
            mcp_server.get_profile_credentials_with_scope("test", "https://www.googleapis.com/auth/drive")

    @patch("mcp_server.get_profile_credentials")
    def test_returns_creds_when_scope_present(self, mock_get_creds):
        mock_creds = Mock()
        mock_creds.scopes = ["https://www.googleapis.com/auth/drive"]
        mock_get_creds.return_value = mock_creds
        result = mcp_server.get_profile_credentials_with_scope("test", "https://www.googleapis.com/auth/drive")
        assert result == mock_creds
