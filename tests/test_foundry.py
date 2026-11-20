import asyncio

from app.foundry import LiveClient
from app.settings import Settings


def test_live_client_initializes_without_network_on_app_service(monkeypatch):
    monkeypatch.setenv("AZURE_TOKEN_CREDENTIALS", "ManagedIdentityCredential")
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://127.0.0.1:8081/identity")
    monkeypatch.setenv("IDENTITY_HEADER", "test-only")
    settings = Settings(azure_openai_base_url="https://example.openai.azure.com/openai/v1/")

    async def open_and_close():
        await LiveClient(settings).close()

    asyncio.run(open_and_close())
