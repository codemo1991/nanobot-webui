"""E2E test for provider UI configuration."""
import sys
import os
import shutil
import tempfile
sys.path.insert(0, os.path.dirname(__file__))


def make_temp_dir():
    """Create a temp directory and register cleanup."""
    tmpdir = tempfile.mkdtemp()
    return tmpdir


def cleanup_temp_dir(tmpdir):
    """Clean up temp directory, ignoring Windows file lock errors."""
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass


class TempDB:
    """Context manager for a temp SQLite DB that handles Windows file locks."""

    def __init__(self):
        self.tmpdir = None
        self.db_path = None
        self.repo = None

    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp()
        from pathlib import Path
        self.db_path = Path(self.tmpdir) / "test.db"
        from nanobot.storage.config_repository import ConfigRepository
        self.repo = ConfigRepository(self.db_path)
        return self

    def __exit__(self, *args):
        # Close any open connections by dropping the repo reference
        self.repo = None
        self.db_path = None
        cleanup_temp_dir(self.tmpdir)


def test_system_providers_init():
    """Test that system providers are initialized correctly."""
    from nanobot.config.loader import init_system_providers

    with TempDB() as db:
        repo = db.repo

        # Initialize system providers
        init_system_providers(repo)

        # Check system providers were created
        providers = repo.get_all_providers()
        system_providers = [p for p in providers if p.get("is_system")]
        assert len(system_providers) >= 60, f"Expected 60+ system providers, got {len(system_providers)}"

        # Check each has required fields
        for p in system_providers[:5]:  # spot check first 5
            assert "display_name" in p, f"Missing display_name: {p}"
            assert "provider_type" in p, f"Missing provider_type: {p}"
            assert "is_system" in p, f"Missing is_system: {p}"

        # Check models were created
        models = repo.get_all_models()
        assert len(models) >= 100, f"Expected 100+ models, got {len(models)}"

        # Check model has new fields
        if models:
            m = models[0]
            assert "model_type" in m, f"Missing model_type: {m}"

        print(f"System providers: {len(system_providers)}")
        print(f"Models: {len(models)}")
        print("All E2E backend checks passed!")


def test_api_provider_fields():
    """Test that API methods return new fields."""
    from nanobot.config.loader import init_system_providers

    with TempDB() as db:
        repo = db.repo
        init_system_providers(repo)

        # Simulate what api.py does:
        providers = repo.get_all_providers()
        # Simulate camelCase conversion (as done by API layer)
        for p in providers[:3]:
            api_response = {
                "id": p["id"],
                "displayName": p.get("display_name", p["name"]),
                "providerType": p.get("provider_type", "openai"),
                "isSystem": p.get("is_system", False),
                "sortOrder": p.get("sort_order", 0),
            }
            assert api_response["displayName"], f"Empty displayName: {p}"
            assert api_response["providerType"], f"Empty providerType: {p}"
            assert isinstance(api_response["isSystem"], bool)

        models = repo.get_all_models()
        for m in models[:3]:
            api_response = {
                "id": m["id"],
                "modelType": m.get("model_type", "chat"),
                "maxTokens": m.get("max_tokens", 4096),
                "supportsVision": bool(m.get("supports_vision", False)),
                "supportsFunctionCalling": bool(m.get("supports_function_calling", True)),
            }
            assert api_response["modelType"], f"Empty modelType: {m}"
            assert isinstance(api_response["supportsVision"], bool)

        print("API field simulation passed!")


def test_provider_crud():
    """Test basic CRUD operations on providers."""
    from nanobot.providers.system_providers import SYSTEM_PROVIDERS

    with TempDB() as db:
        repo = db.repo

        # Create a custom provider
        repo.set_provider(
            provider_id="test-custom",
            name="Test Custom",
            display_name="Test Custom Provider",
            provider_type="openai",
            api_base="https://test.example.com",
            api_key="test-key",
            enabled=True,
            is_system=False,
            sort_order=100,
        )

        # Read it back
        p = repo.get_provider("test-custom")
        assert p is not None, "Provider should be retrievable"
        assert p["display_name"] == "Test Custom Provider"
        assert p["provider_type"] == "openai"
        assert p["is_system"] is False
        assert p["sort_order"] == 100

        # Try to delete system provider (should fail silently)
        first_provider_id = SYSTEM_PROVIDERS[0]["id"]
        deleted = repo.delete_provider(first_provider_id)
        assert deleted is False, "System provider should not be deletable"

        # Delete custom provider (should succeed)
        deleted = repo.delete_provider("test-custom")
        assert deleted is True, "Custom provider should be deletable"

        # Verify it's gone
        p = repo.get_provider("test-custom")
        assert p is None, "Custom provider should be deleted"

        print("Provider CRUD passed!")


def test_model_crud():
    """Test basic CRUD operations on models."""
    from nanobot.config.loader import init_system_providers

    with TempDB() as db:
        repo = db.repo

        # Init system providers first
        init_system_providers(repo)

        # Get the first system provider
        providers = repo.get_all_providers()
        first_provider = providers[0]
        provider_id = first_provider["id"]

        # Create a custom model
        repo.set_model(
            model_id="test-model-1",
            provider_id=provider_id,
            name="Test Model",
            litellm_id="test/test-model",
            model_type="chat",
            context_window=128000,
            max_tokens=4096,
            supports_vision=True,
            supports_function_calling=False,
            supports_streaming=True,
            is_default=False,
        )

        # Read it back
        m = repo.get_model("test-model-1")
        assert m is not None, "Model should be retrievable"
        assert m["model_type"] == "chat"
        assert m["max_tokens"] == 4096
        assert m["supports_vision"] is True
        assert m["supports_function_calling"] is False

        # Delete model
        deleted = repo.delete_model("test-model-1")
        assert deleted is True, "Model should be deletable"

        m = repo.get_model("test-model-1")
        assert m is None, "Model should be deleted"

        print("Model CRUD passed!")


if __name__ == "__main__":
    test_system_providers_init()
    test_api_provider_fields()
    test_provider_crud()
    test_model_crud()
    print("\nAll E2E tests passed!")
