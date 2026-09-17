#!/usr/bin/env python3
import os
import sys
import json
import tempfile
import shutil
import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
import httpx

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


class TestResolveCacheFilePath:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)
        self.cache_dir = mcp_server.get_profile_cache_dir("test_profile")

    def teardown_method(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)

    def _write_cache_file(self, cache_key, content=b"hello"):
        path = os.path.join(self.cache_dir, cache_key)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def test_resolves_existing_file(self):
        expected_path = self._write_cache_file("file123")
        result = mcp_server.resolve_cache_file_path("test_profile", "file123")
        assert os.path.realpath(result) == os.path.realpath(expected_path)

    def test_resolves_export_style_cache_key(self):
        expected_path = self._write_cache_file("file123.export.pdf")
        result = mcp_server.resolve_cache_file_path("test_profile", "file123.export.pdf")
        assert os.path.realpath(result) == os.path.realpath(expected_path)

    def test_raises_file_not_found_when_missing(self):
        with pytest.raises(FileNotFoundError):
            mcp_server.resolve_cache_file_path("test_profile", "does_not_exist")

    def test_rejects_empty_cache_key(self):
        with pytest.raises(ValueError):
            mcp_server.resolve_cache_file_path("test_profile", "")

    def test_rejects_path_traversal_with_dotdot(self):
        with pytest.raises(ValueError):
            mcp_server.resolve_cache_file_path("test_profile", "../../etc/passwd")

    def test_rejects_nested_path_separators(self):
        with pytest.raises(ValueError):
            mcp_server.resolve_cache_file_path("test_profile", "subdir/file123")

    def test_rejects_bare_dotdot(self):
        with pytest.raises(ValueError):
            mcp_server.resolve_cache_file_path("test_profile", "..")


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

    @pytest.mark.asyncio
    async def test_returns_profile_for_mapped_session_via_header(self):
        mock_ctx = Mock()
        mock_request = Mock()
        mock_request.headers = {"mcp-session-id": "header_sess_456"}
        mock_request.query_params = {}
        mock_ctx.request_context.request = mock_request
        mcp_server.session_to_profile["header_sess_456"] = "header_profile"
        result = await mcp_server.get_profile_name(mock_ctx)
        assert result == "header_profile"
        del mcp_server.session_to_profile["header_sess_456"]


class TestExtractTokenFromRequest:
    def test_extracts_token_from_query_params(self):
        mock_request = Mock()
        mock_request.query_params = {"token": "token_abc"}
        mock_request.headers = {}
        assert mcp_server.extract_token_from_request(mock_request) == "token_abc"

    def test_extracts_token_from_bearer_authorization_header(self):
        mock_request = Mock()
        mock_request.query_params = {}
        mock_request.headers = {"authorization": "Bearer token_xyz"}
        assert mcp_server.extract_token_from_request(mock_request) == "token_xyz"

    def test_extracts_token_with_case_insensitive_bearer(self):
        mock_request = Mock()
        mock_request.query_params = {}
        mock_request.headers = {"authorization": "bearer  token_123 "}
        assert mcp_server.extract_token_from_request(mock_request) == "token_123"

    def test_returns_none_when_no_token_provided(self):
        mock_request = Mock()
        mock_request.query_params = {}
        mock_request.headers = {}
        assert mcp_server.extract_token_from_request(mock_request) is None

    def test_returns_none_when_authorization_not_bearer(self):
        mock_request = Mock()
        mock_request.query_params = {}
        mock_request.headers = {"authorization": "Basic dXNlcjpwYXNz"}
        assert mcp_server.extract_token_from_request(mock_request) is None


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


class TestResolveExportMimeType:
    def test_doc_pdf_resolves(self):
        assert mcp_server.resolve_export_mime_type(
            "application/vnd.google-apps.document", "pdf"
        ) == "application/pdf"

    def test_doc_docx_resolves(self):
        assert mcp_server.resolve_export_mime_type(
            "application/vnd.google-apps.document", "docx"
        ) == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def test_doc_txt_resolves(self):
        assert mcp_server.resolve_export_mime_type(
            "application/vnd.google-apps.document", "txt"
        ) == "text/plain"

    def test_sheet_xlsx_resolves(self):
        assert mcp_server.resolve_export_mime_type(
            "application/vnd.google-apps.spreadsheet", "xlsx"
        ) == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def test_sheet_csv_resolves(self):
        assert mcp_server.resolve_export_mime_type(
            "application/vnd.google-apps.spreadsheet", "csv"
        ) == "text/csv"

    def test_slide_pdf_resolves(self):
        assert mcp_server.resolve_export_mime_type(
            "application/vnd.google-apps.presentation", "pdf"
        ) == "application/pdf"

    def test_slide_pptx_resolves(self):
        assert mcp_server.resolve_export_mime_type(
            "application/vnd.google-apps.presentation", "pptx"
        ) == "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    def test_format_matching_is_case_insensitive(self):
        assert mcp_server.resolve_export_mime_type(
            "application/vnd.google-apps.document", "PDF"
        ) == "application/pdf"

    def test_non_google_apps_mime_type_raises_with_download_hint(self):
        with pytest.raises(ValueError, match="google_drive_download_file"):
            mcp_server.resolve_export_mime_type("application/pdf", "pdf")

    def test_unsupported_format_for_valid_type_raises_with_valid_options(self):
        with pytest.raises(ValueError) as exc_info:
            mcp_server.resolve_export_mime_type("application/vnd.google-apps.document", "xlsx")
        assert "docx" in str(exc_info.value)
        assert "pdf" in str(exc_info.value)

    def test_unknown_google_apps_mime_type_raises(self):
        with pytest.raises(ValueError, match="No export formats are known"):
            mcp_server.resolve_export_mime_type("application/vnd.google-apps.folder", "pdf")


class TestGoogleDriveExportFile:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)
        os.makedirs(os.path.join("profiles", "test_profile"), exist_ok=True)

    def teardown_method(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)

    def _mock_ctx(self):
        mock_ctx = Mock()
        mock_ctx.request_context.request = None
        return mock_ctx

    def _mock_drive_service(self, file_meta, export_bytes=b"exported-bytes"):
        mock_service = MagicMock()
        mock_service.files.return_value.get.return_value.execute.return_value = file_meta
        mock_service.files.return_value.export_media.return_value = Mock()

        def fake_media_download(fh, request):
            fh.write(export_bytes)
            mock_downloader = Mock()
            mock_downloader.next_chunk.return_value = (Mock(), True)
            return mock_downloader

        return mock_service, fake_media_download

    @patch("mcp_server.get_profile_credentials_with_scope")
    @patch("mcp_server.build")
    @pytest.mark.asyncio
    async def test_export_doc_to_pdf_happy_path(self, mock_build, mock_get_creds):
        os.environ["DEFAULT_PROFILE"] = "test_profile"
        file_meta = {
            "name": "MyDoc",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-01-01T00:00:00.000Z",
        }
        mock_service, fake_media_download = self._mock_drive_service(file_meta)
        mock_build.return_value = mock_service

        with patch("googleapiclient.http.MediaIoBaseDownload", side_effect=fake_media_download):
            result = await mcp_server.google_drive_export_file(self._mock_ctx(), "file123", "pdf")

        del os.environ["DEFAULT_PROFILE"]
        data = json.loads(result)
        assert data["status"] == "success"
        assert data["cached"] is False
        assert data["export_mime_type"] == "application/pdf"
        assert data["single_sheet_or_slide_only"] is False
        mock_service.files.return_value.export_media.assert_called_once_with(
            fileId="file123", mimeType="application/pdf"
        )
        assert os.path.exists(data["local_path"])
        with open(data["local_path"], "rb") as f:
            assert f.read() == b"exported-bytes"

    @patch("mcp_server.get_profile_credentials_with_scope")
    @patch("mcp_server.build")
    @pytest.mark.asyncio
    async def test_export_sheet_to_csv_flags_single_sheet_only(self, mock_build, mock_get_creds):
        os.environ["DEFAULT_PROFILE"] = "test_profile"
        file_meta = {
            "name": "MySheet",
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "modifiedTime": "2026-01-01T00:00:00.000Z",
        }
        mock_service, fake_media_download = self._mock_drive_service(file_meta)
        mock_build.return_value = mock_service

        with patch("googleapiclient.http.MediaIoBaseDownload", side_effect=fake_media_download):
            result = await mcp_server.google_drive_export_file(self._mock_ctx(), "sheet123", "csv")

        del os.environ["DEFAULT_PROFILE"]
        data = json.loads(result)
        assert data["status"] == "success"
        assert data["export_mime_type"] == "text/csv"
        assert data["single_sheet_or_slide_only"] is True

    @patch("mcp_server.get_profile_credentials_with_scope")
    @patch("mcp_server.build")
    @pytest.mark.asyncio
    async def test_export_slides_to_pptx_happy_path(self, mock_build, mock_get_creds):
        os.environ["DEFAULT_PROFILE"] = "test_profile"
        file_meta = {
            "name": "MyDeck",
            "mimeType": "application/vnd.google-apps.presentation",
            "modifiedTime": "2026-01-01T00:00:00.000Z",
        }
        mock_service, fake_media_download = self._mock_drive_service(file_meta)
        mock_build.return_value = mock_service

        with patch("googleapiclient.http.MediaIoBaseDownload", side_effect=fake_media_download):
            result = await mcp_server.google_drive_export_file(self._mock_ctx(), "deck123", "pptx")

        del os.environ["DEFAULT_PROFILE"]
        data = json.loads(result)
        assert data["status"] == "success"
        assert data["export_mime_type"] == (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )

    @patch("mcp_server.get_profile_credentials_with_scope")
    @patch("mcp_server.build")
    @pytest.mark.asyncio
    async def test_cache_hit_skips_export_media_call(self, mock_build, mock_get_creds):
        os.environ["DEFAULT_PROFILE"] = "test_profile"
        file_meta = {
            "name": "MyDoc",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-01-01T00:00:00.000Z",
        }
        mock_service, fake_media_download = self._mock_drive_service(file_meta)
        mock_build.return_value = mock_service

        with patch("googleapiclient.http.MediaIoBaseDownload", side_effect=fake_media_download):
            first = await mcp_server.google_drive_export_file(self._mock_ctx(), "file123", "pdf")
        first_data = json.loads(first)
        assert first_data["cached"] is False

        # Second call: same modifiedTime -> should hit cache without calling export_media again.
        mock_service.files.return_value.export_media.reset_mock()
        with patch("googleapiclient.http.MediaIoBaseDownload", side_effect=fake_media_download):
            second = await mcp_server.google_drive_export_file(self._mock_ctx(), "file123", "pdf")

        del os.environ["DEFAULT_PROFILE"]
        second_data = json.loads(second)
        assert second_data["cached"] is True
        mock_service.files.return_value.export_media.assert_not_called()

    @patch("mcp_server.get_profile_credentials_with_scope")
    @patch("mcp_server.build")
    @pytest.mark.asyncio
    async def test_cache_miss_on_source_modified_time_change(self, mock_build, mock_get_creds):
        os.environ["DEFAULT_PROFILE"] = "test_profile"
        file_meta = {
            "name": "MyDoc",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-01-01T00:00:00.000Z",
        }
        mock_service, fake_media_download = self._mock_drive_service(file_meta)
        mock_build.return_value = mock_service

        with patch("googleapiclient.http.MediaIoBaseDownload", side_effect=fake_media_download):
            await mcp_server.google_drive_export_file(self._mock_ctx(), "file123", "pdf")

        # Source doc changed since the export was cached.
        file_meta["modifiedTime"] = "2026-02-01T00:00:00.000Z"
        mock_service.files.return_value.export_media.reset_mock()
        with patch("googleapiclient.http.MediaIoBaseDownload", side_effect=fake_media_download):
            result = await mcp_server.google_drive_export_file(self._mock_ctx(), "file123", "pdf")

        del os.environ["DEFAULT_PROFILE"]
        data = json.loads(result)
        assert data["cached"] is False
        mock_service.files.return_value.export_media.assert_called_once()

    @patch("mcp_server.get_profile_credentials_with_scope")
    @patch("mcp_server.build")
    @pytest.mark.asyncio
    async def test_unsupported_format_returns_clean_error_without_calling_api(self, mock_build, mock_get_creds):
        os.environ["DEFAULT_PROFILE"] = "test_profile"
        file_meta = {
            "name": "MyDoc",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-01-01T00:00:00.000Z",
        }
        mock_service, _ = self._mock_drive_service(file_meta)
        mock_build.return_value = mock_service

        result = await mcp_server.google_drive_export_file(self._mock_ctx(), "file123", "xlsx")

        del os.environ["DEFAULT_PROFILE"]
        assert result.startswith("Error:")
        assert "xlsx" in result
        mock_service.files.return_value.export_media.assert_not_called()

    @patch("mcp_server.get_profile_credentials_with_scope")
    @patch("mcp_server.build")
    @pytest.mark.asyncio
    async def test_non_google_apps_file_returns_clean_error(self, mock_build, mock_get_creds):
        os.environ["DEFAULT_PROFILE"] = "test_profile"
        file_meta = {
            "name": "regular.bin",
            "mimeType": "application/octet-stream",
            "modifiedTime": "2026-01-01T00:00:00.000Z",
        }
        mock_service, _ = self._mock_drive_service(file_meta)
        mock_build.return_value = mock_service

        result = await mcp_server.google_drive_export_file(self._mock_ctx(), "file123", "pdf")

        del os.environ["DEFAULT_PROFILE"]
        assert result.startswith("Error:")
        assert "google_drive_download_file" in result
        mock_service.files.return_value.export_media.assert_not_called()

    @patch("mcp_server.get_profile_credentials_with_scope")
    @patch("mcp_server.build")
    @pytest.mark.asyncio
    async def test_export_size_limit_error_is_caught_and_actionable(self, mock_build, mock_get_creds):
        os.environ["DEFAULT_PROFILE"] = "test_profile"
        file_meta = {
            "name": "HugeDoc",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-01-01T00:00:00.000Z",
        }
        mock_service = MagicMock()
        mock_service.files.return_value.get.return_value.execute.return_value = file_meta
        mock_service.files.return_value.export_media.return_value = Mock()
        mock_build.return_value = mock_service

        from googleapiclient.errors import HttpError

        mock_resp = Mock()
        mock_resp.status = 403
        # Shaped like a real Drive API error body: top-level "message" plus an
        # "errors" array carrying the specific reason code.
        size_limit_error = HttpError(
            mock_resp,
            b'{"error": {"code": 403, "message": "This file is too large to be '
            b'exported.", "errors": [{"domain": "global", "reason": '
            b'"exportSizeLimitExceeded", "message": "This file is too large to '
            b'be exported."}]}}',
        )

        def fake_media_download(fh, request):
            mock_downloader = Mock()
            mock_downloader.next_chunk.side_effect = size_limit_error
            return mock_downloader

        with patch("googleapiclient.http.MediaIoBaseDownload", side_effect=fake_media_download):
            result = await mcp_server.google_drive_export_file(self._mock_ctx(), "file123", "docx")

        del os.environ["DEFAULT_PROFILE"]
        assert result.startswith("Error:")
        assert "10 MB" in result


class TestServerInstructions:
    def test_instructions_constant_is_nonempty_string(self):
        assert isinstance(mcp_server.SERVER_INSTRUCTIONS, str)
        assert mcp_server.SERVER_INSTRUCTIONS.strip() != ""

    def test_fastmcp_instance_exposes_instructions(self):
        # FastMCP.instructions is backed by the low-level Server, which feeds
        # InitializeResult.instructions in the MCP initialize handshake.
        assert mcp_server.mcp.instructions == mcp_server.SERVER_INSTRUCTIONS

    def test_instructions_mention_read_before_write_ordering(self):
        text = mcp_server.SERVER_INSTRUCTIONS
        assert "google_drive_list_files" in text
        assert "google_tasks_delete_task" in text


class TestStreamableHTTPTransport:
    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)
        os.makedirs("profiles/test_user", exist_ok=True)
        with open("profiles/test_user/.env", "w") as f:
            f.write("PROFILE_TOKEN=secret_token_123\n")

    def teardown_method(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)
        mcp_server.session_to_profile.clear()

    @pytest.mark.asyncio
    async def test_unauthorized_when_token_missing(self):
        async with mcp_server.streamable_session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
            ) as client:
                resp = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
                assert resp.status_code == 401
                assert "Unauthorized: Missing token" in resp.text

    @pytest.mark.asyncio
    async def test_unauthorized_when_token_invalid(self):
        async with mcp_server.streamable_session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
            ) as client:
                resp = await client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                    headers={"Authorization": "Bearer wrong_token"},
                )
                assert resp.status_code == 401
                assert "Unauthorized: Invalid token" in resp.text

    @pytest.mark.asyncio
    async def test_session_creation_with_bearer_token(self):
        async with mcp_server.streamable_session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
            ) as client:
                init_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0"},
                    },
                }
                resp = await client.post(
                    "/mcp",
                    json=init_payload,
                    headers={
                        "Authorization": "Bearer secret_token_123",
                        "Accept": "application/json, text/event-stream",
                    },
                )
                assert resp.status_code == 200
                session_id = resp.headers.get("mcp-session-id")
                assert session_id is not None
                assert mcp_server.session_to_profile.get(session_id) == "test_user"

    @pytest.mark.asyncio
    async def test_session_creation_with_query_param_token(self):
        async with mcp_server.streamable_session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
            ) as client:
                init_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0"},
                    },
                }
                resp = await client.post(
                    "/mcp?token=secret_token_123",
                    json=init_payload,
                    headers={"Accept": "application/json, text/event-stream"},
                )
                assert resp.status_code == 200
                session_id = resp.headers.get("mcp-session-id")
                assert session_id is not None
                assert mcp_server.session_to_profile.get(session_id) == "test_user"

    @pytest.mark.asyncio
    async def test_existing_session_tool_list_and_delete(self):
        async with mcp_server.streamable_session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
            ) as client:
                # 1. Initialize
                init_resp = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test-client", "version": "1.0"},
                        },
                    },
                    headers={
                        "Authorization": "Bearer secret_token_123",
                        "Accept": "application/json, text/event-stream",
                    },
                )
                assert init_resp.status_code == 200
                session_id = init_resp.headers.get("mcp-session-id")

                # 2. Initialized notification
                notif_resp = await client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers={
                        "mcp-session-id": session_id,
                        "Authorization": "Bearer secret_token_123",
                        "Accept": "application/json, text/event-stream",
                    },
                )
                assert notif_resp.status_code == 202

                # 3. List tools
                list_resp = await client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    headers={
                        "mcp-session-id": session_id,
                        "Authorization": "Bearer secret_token_123",
                        "Accept": "application/json, text/event-stream",
                    },
                )
                assert list_resp.status_code == 200
                assert "google_drive_list_files" in list_resp.text

                # 4. DELETE session terminates and cleans up
                del_resp = await client.delete(
                    "/mcp",
                    headers={
                        "mcp-session-id": session_id,
                        "Authorization": "Bearer secret_token_123",
                    },
                )
                assert del_resp.status_code == 200
                assert session_id not in mcp_server.session_to_profile

    @pytest.mark.asyncio
    async def test_unknown_session_id_returns_404(self):
        async with mcp_server.streamable_session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
            ) as client:
                resp = await client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers={
                        "mcp-session-id": "nonexistent_session_id",
                        "Accept": "application/json, text/event-stream",
                    },
                )
                assert resp.status_code == 404


class TestFileDownloadRoute:
    """GM-4: GET /files/{cache_key} lets a remote MCP client retrieve the
    bytes of a file previously written by google_drive_download_file /
    google_drive_export_file, which otherwise only return a server-local
    "local_path" the client has no way to read."""

    def setup_method(self):
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)
        os.makedirs("profiles/test_user", exist_ok=True)
        with open("profiles/test_user/.env", "w") as f:
            f.write("PROFILE_TOKEN=secret_token_123\n")
        self.cache_dir = mcp_server.get_profile_cache_dir("test_user")

    def teardown_method(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir)

    def _write_cache_file(self, cache_key, content=b"file-bytes", metadata=None):
        with open(os.path.join(self.cache_dir, cache_key), "wb") as f:
            f.write(content)
        if metadata is not None:
            with open(os.path.join(self.cache_dir, f"{cache_key}.json"), "w") as f:
                json.dump(metadata, f)

    @pytest.mark.asyncio
    async def test_unauthorized_when_token_missing(self):
        self._write_cache_file("file123")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
        ) as client:
            resp = await client.get("/files/file123")
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_unauthorized_when_token_invalid(self):
        self._write_cache_file("file123")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
        ) as client:
            resp = await client.get("/files/file123", headers={"Authorization": "Bearer wrong_token"})
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_404_for_missing_cache_key(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
        ) as client:
            resp = await client.get("/files/does_not_exist", params={"token": "secret_token_123"})
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_downloads_cached_file_bytes_with_query_token(self):
        self._write_cache_file(
            "file123",
            content=b"hello world",
            metadata={"name": "greeting.txt", "mimeType": "text/plain"},
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
        ) as client:
            resp = await client.get("/files/file123", params={"token": "secret_token_123"})
            assert resp.status_code == 200
            assert resp.content == b"hello world"
            assert resp.headers["content-type"].startswith("text/plain")
            assert "greeting.txt" in resp.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_downloads_exported_file_using_export_mime_type(self):
        self._write_cache_file(
            "file123.export.pdf",
            content=b"%PDF-fake",
            metadata={"name": "MyDoc", "mimeType": "application/vnd.google-apps.document", "export_mime_type": "application/pdf"},
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
        ) as client:
            resp = await client.get(
                "/files/file123.export.pdf",
                headers={"Authorization": "Bearer secret_token_123"},
            )
            assert resp.status_code == 200
            assert resp.content == b"%PDF-fake"
            assert resp.headers["content-type"].startswith("application/pdf")

    @pytest.mark.asyncio
    async def test_rejects_path_traversal_cache_key(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
        ) as client:
            resp = await client.get(
                "/files/..%2F..%2Fetc%2Fpasswd", params={"token": "secret_token_123"}
            )
            assert resp.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_missing_metadata_sidecar_falls_back_to_generic_content_type(self):
        self._write_cache_file("file123")  # no metadata sidecar written
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
        ) as client:
            resp = await client.get("/files/file123", params={"token": "secret_token_123"})
            assert resp.status_code == 200
            assert resp.content == b"file-bytes"


class TestTransportCoexistence:
    def test_routes_configured_on_starlette_app(self):
        paths = [route.path for route in mcp_server.app.routes]
        assert "/mcp" in paths
        assert "/sse" in paths
        assert "/messages" in paths

    @pytest.mark.asyncio
    async def test_both_endpoints_accessible_on_same_app(self):
        async with mcp_server.streamable_session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_server.app), base_url="http://testserver"
            ) as client:
                # SSE endpoint rejects unauthenticated GET
                sse_resp = await client.get("/sse")
                assert sse_resp.status_code == 401

                # Streamable HTTP endpoint rejects unauthenticated POST
                mcp_resp = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
                assert mcp_resp.status_code == 401

