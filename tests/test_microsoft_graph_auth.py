from __future__ import annotations

import unittest
from types import SimpleNamespace

from shared.integrations.microsoft_graph import GraphAuthenticationError, GraphClient


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.content = b"{}" if payload is not None else b""
        self.headers: dict[str, str] = {}

    def json(self) -> dict:
        return self._payload


class FakeRequests:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def request(self, _method: str, _url: str, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeApplication:
    def __init__(self, token_results: list[dict | Exception]) -> None:
        self.token_results = token_results
        self.calls = 0

    def acquire_token_for_client(self, scopes: list[str]) -> dict:
        del scopes
        result = self.token_results[self.calls]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result


class GraphClientAuthenticationTests(unittest.TestCase):
    def build_client(
        self,
        responses: list[FakeResponse],
        token_results: list[dict | Exception],
        clock: list[float] | None = None,
    ) -> tuple[GraphClient, FakeRequests, FakeApplication]:
        current_clock = clock if clock is not None else [0.0]
        client = GraphClient("tenant", "client", "secret", clock=lambda: current_clock[0])
        requests = FakeRequests(responses)
        application = FakeApplication(token_results)
        client._requests = requests
        client._msal = SimpleNamespace(ConfidentialClientApplication=lambda **_kwargs: application)
        return client, requests, application

    def test_valid_client_credential_token_is_reused_until_refresh_window(self) -> None:
        clock = [0.0]
        client, _requests, application = self.build_client(
            [],
            [{"access_token": "first", "expires_in": 3600}, {"access_token": "second", "expires_in": 3600}],
            clock,
        )

        self.assertEqual(client.token(), "first")
        clock[0] = 3000.0
        self.assertEqual(client.token(), "first")
        clock[0] = 3541.0
        self.assertEqual(client.token(), "second")
        self.assertEqual(application.calls, 2)

    def test_graph_401_reacquires_once_and_replays_request(self) -> None:
        client, requests, application = self.build_client(
            [FakeResponse(401, text="expired token"), FakeResponse(200, {"value": []})],
            [{"access_token": "first", "expires_in": 3600}, {"access_token": "second", "expires_in": 3600}],
        )

        self.assertEqual(client.request("GET", "/me"), {"value": []})
        self.assertEqual(application.calls, 2)
        self.assertEqual(len(requests.calls), 2)
        self.assertEqual(requests.calls[0]["headers"]["Authorization"], "Bearer first")
        self.assertEqual(requests.calls[1]["headers"]["Authorization"], "Bearer second")

    def test_repeated_graph_401_is_sanitized(self) -> None:
        client, _requests, _application = self.build_client(
            [FakeResponse(401, text="secret-token-and-mailbox-data"), FakeResponse(401, text="secret-token-and-mailbox-data")],
            [{"access_token": "first", "expires_in": 3600}, {"access_token": "second", "expires_in": 3600}],
        )

        with self.assertRaises(GraphAuthenticationError) as raised:
            client.request("GET", "/me")

        self.assertNotIn("secret-token-and-mailbox-data", str(raised.exception))
        self.assertEqual(
            str(raised.exception),
            "Microsoft Graph authentication is unavailable. Check application credentials.",
        )

    def test_msal_client_construction_value_error_is_sanitized(self) -> None:
        client = GraphClient("tenant-value", "client-value", "secret-value")
        client._msal = SimpleNamespace(
            ConfidentialClientApplication=lambda **_kwargs: (_ for _ in ()).throw(
                ValueError("tenant-value client-value secret-value")
            )
        )

        with self.assertRaises(GraphAuthenticationError) as raised:
            client.token()

        self.assertEqual(
            str(raised.exception),
            "Microsoft Graph authentication is unavailable. Check application credentials.",
        )
        self.assertNotIn("tenant-value", str(raised.exception))
        self.assertNotIn("client-value", str(raised.exception))
        self.assertNotIn("secret-value", str(raised.exception))

    def test_msal_token_acquisition_value_error_is_sanitized(self) -> None:
        client, _requests, _application = self.build_client(
            [],
            [ValueError("tenant-value client-value secret-value")],
        )

        with self.assertRaises(GraphAuthenticationError) as raised:
            client.token()

        self.assertEqual(
            str(raised.exception),
            "Microsoft Graph authentication is unavailable. Check application credentials.",
        )
        self.assertNotIn("tenant-value", str(raised.exception))
        self.assertNotIn("client-value", str(raised.exception))
        self.assertNotIn("secret-value", str(raised.exception))
