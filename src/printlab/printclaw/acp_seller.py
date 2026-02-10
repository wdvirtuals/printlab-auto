"""PrintClaw ACP Seller — listens for incoming jobs via the Virtuals ACP network."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class ACPSellerAgent:
    """ACP seller that fulfills jobs by calling the PrintLab REST API.

    The virtuals-acp SDK uses synchronous python-socketio with threaded callbacks.
    We bridge into the async event loop via ``asyncio.run_coroutine_threadsafe``.
    """

    def __init__(
        self,
        wallet_address: str,
        wallet_private_key: str,
        entity_id: int,
        api_client,  # PrintLabAPIClient
        on_event: Optional[Callable[[str], Awaitable[None]]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        self.api = api_client
        self.on_event = on_event
        self.loop = loop or asyncio.get_event_loop()
        self.acp = None

        self._wallet_address = wallet_address
        self._wallet_private_key = wallet_private_key
        self._entity_id = entity_id

    def start(self):
        """Initialize the ACP SDK and connect the WebSocket listener."""
        from virtuals_acp.client import VirtualsACP
        from virtuals_acp.contract_clients.contract_client_v2 import ACPContractClientV2
        from virtuals_acp.configs.configs import BASE_MAINNET_ACP_X402_CONFIG_V2

        contract_client = ACPContractClientV2(
            agent_wallet_address=self._wallet_address,
            wallet_private_key=self._wallet_private_key,
            entity_id=self._entity_id,
            config=BASE_MAINNET_ACP_X402_CONFIG_V2,
        )

        self.acp = VirtualsACP(
            acp_contract_clients=contract_client,
            on_new_task=self._handle_new_task,
            on_evaluate=self._handle_evaluate,
        )
        self._emit_event("ACP seller connected and listening for jobs")

    # --- Thread-safe helpers ---

    def _emit_event(self, message: str):
        """Notify the TG bot owner (thread-safe)."""
        if self.on_event and self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.on_event(message), self.loop)

    def _run_async(self, coro, timeout: float = 60):
        """Run an async coroutine from a sync callback thread."""
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=timeout)

    # --- Job handlers (called from SDK threads) ---

    def _handle_new_task(self, job, memo_to_sign=None):
        """Called by the SDK when a buyer initiates a job."""
        try:
            requirement = job.requirement
            job_name = job.name or ""

            self._emit_event(
                f"New job #{job.id}: {job_name}\n"
                f"From: {job.client_address[:12]}...\n"
                f"Requirement: {requirement}"
            )

            # Accept the job
            job.accept(f"PrintClaw accepting job #{job.id}")

            # Route to the right handler
            name_lower = job_name.lower()
            if "search" in name_lower:
                self._handle_search(job, requirement)
            elif "print" in name_lower:
                self._handle_print(job, requirement)
            elif "status" in name_lower:
                self._handle_status(job)
            else:
                job.reject(f"Unknown offering: {job_name}")
                self._emit_event(f"Job #{job.id} rejected: unknown offering '{job_name}'")

        except Exception as e:
            logger.exception("Error handling ACP job #%s", getattr(job, "id", "?"))
            self._emit_event(f"Error on job #{getattr(job, 'id', '?')}: {e}")
            try:
                job.reject(f"Internal error: {e}")
            except Exception:
                pass

    def _handle_search(self, job, requirement):
        if isinstance(requirement, dict):
            query = requirement.get("query", "")
            limit = requirement.get("limit", 5)
        else:
            query = str(requirement)
            limit = 5

        result = self._run_async(self.api.search(query, limit), timeout=30)
        job.deliver(json.dumps({"models": result}))
        self._emit_event(f"Job #{job.id} delivered: {len(result)} results for '{query}'")

    def _handle_print(self, job, requirement):
        if isinstance(requirement, dict):
            model_id = requirement.get("model_id")
            model_url = requirement.get("model_url")
            filename = requirement.get("filename")
            ams_mapping = requirement.get("ams_mapping")
        else:
            model_id = str(requirement)
            model_url = None
            filename = None
            ams_mapping = None

        result = self._run_async(
            self.api.print_model(model_id, model_url, filename, ams_mapping),
            timeout=120,
        )
        job.deliver(json.dumps(result))
        self._emit_event(f"Job #{job.id} delivered: print result = {result}")

    def _handle_status(self, job):
        result = self._run_async(self.api.status(), timeout=15)
        job.deliver(json.dumps(result))
        self._emit_event(
            f"Job #{job.id} delivered: status = {result.get('state', 'unknown')}"
        )

    def _handle_evaluate(self, job):
        """Auto-accept evaluations."""
        job.evaluate(True, "Auto-evaluated by PrintClaw")
        self._emit_event(f"Job #{job.id} evaluation: auto-accepted")

    def shutdown(self):
        """Disconnect the ACP WebSocket."""
        if self.acp and hasattr(self.acp, "sio"):
            try:
                self.acp.sio.disconnect()
            except Exception:
                pass
