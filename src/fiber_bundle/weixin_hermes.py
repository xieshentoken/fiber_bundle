"""Build a reviewable pinned Hermes candidate; never edit an installed gateway."""

import ast
import hashlib

from .config import InvalidInput

SOURCE_SHA256 = "9987588496200d1b2c76ac0cd968a2461176201f7874ff90d93b9084a6f3b6e5"


def candidate(source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise InvalidInput("Hermes Weixin source changed; review integration before rebuilding")
    text = source.decode("utf-8")
    tree = ast.parse(text)
    adapter = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WeixinAdapter"
    )
    poll = next(
        n for n in adapter.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_poll_loop"
    )
    lines = text.splitlines(keepends=True)
    lines[poll.lineno - 1 : poll.end_lineno] = [
        "    _fiber_capture_version = 1\n\n"
        "    async def _fiber_send_once(self, owner, body):\n"
        "        from fiber_bundle.weixin_hermes_sender import send_once\n"
        "        return await send_once(self, owner, body,\n"
        "                               headers=_headers, base_info=_base_info)\n\n"
        "    async def _fiber_send_result_once(self, owner, body, authorize):\n"
        "        from fiber_bundle.weixin_hermes_sender import send_once\n"
        "        return await send_once(self, owner, body, headers=_headers,\n"
        "                               base_info=_base_info, authorize=authorize)\n\n"
        "    async def _fiber_send_file_once(self, owner, asset, authorize):\n"
        "        from fiber_bundle.weixin_document_sender import send_once\n"
        "        return await send_once(self, owner, asset, authorize,\n"
        "                               headers=_headers, base_info=_base_info)\n\n"
        "    async def _fiber_run_document(self, task_id):\n"
        "        from fiber_bundle.weixin_document_dispatch import dispatch\n"
        "        return await dispatch(self, task_id)\n\n"
        "    async def _fiber_send_prompt(self, prompt_id):\n"
        "        from fiber_bundle.weixin_receiver import preflight\n"
        "        from fiber_bundle.weixin_prompt_delivery import deliver\n"
        "        config, binding = preflight(self)\n"
        "        return await deliver(config, binding, prompt_id, self._fiber_send_once)\n\n"
        "    async def _fiber_send_receipt(self, job_id):\n"
        "        from fiber_bundle.weixin_receiver import preflight\n"
        "        from fiber_bundle.weixin_receipt_delivery import deliver\n"
        "        config, binding = preflight(self)\n"
        "        return await deliver(config, binding, job_id, self._fiber_send_once)\n\n"
        "    async def _fiber_submit_local_query(self, batch_id):\n"
        "        from fiber_bundle.weixin_query_bridge import submit\n"
        "        from fiber_bundle.weixin_receiver import preflight\n"
        "        config, binding = preflight(self)\n"
        "        return submit(config, binding, batch_id)\n\n"
        "    async def _poll_loop(self) -> None:\n"
        "        from fiber_bundle.weixin_receiver import run\n"
        "        await run(self, _get_updates)\n"
    ]
    text = "".join(lines)
    old = """        try:
            if not self._acquire_platform_lock('weixin-bot-token', self._token, 'Weixin bot token'):
                return False
        except Exception as exc:
            logger.debug("[%s] Token lock unavailable (non-fatal): %s", self.name, exc)
"""
    new = """        try:
            from fiber_bundle.weixin_receiver import preflight
            preflight(self)
            live = _LIVE_ADAPTERS.get(self._token)
            if live is not None and live is not self:
                self._set_fatal_error("fiber_weixin_duplicate_adapter",
                                      "Weixin token already has a live adapter in this process",
                                      retryable=False)
                return False
            # This candidate never takes over or terminates an existing gateway.
            self._platform_lock_takeover_allowed = False
            if not self._acquire_platform_lock('weixin-bot-token', self._token, 'Weixin bot token'):
                return False
        except Exception:
            self._set_fatal_error("fiber_weixin_preflight_failed",
                                  "Weixin capture preflight or exclusive lock failed",
                                  retryable=False)
            return False
"""
    if text.count(old) != 1:
        raise InvalidInput("Hermes lock integration point missing")
    text = text.replace(old, new)
    old_release = "        _LIVE_ADAPTERS.pop(self._token, None)\n"
    if text.count(old_release) != 1:
        raise InvalidInput("Hermes live-adapter release point missing")
    text = text.replace(
        old_release,
        "        if _LIVE_ADAPTERS.get(self._token) is self:\n"
        "            _LIVE_ADAPTERS.pop(self._token, None)\n",
    )
    result = text.encode("utf-8")
    compile(result, "weixin-candidate.py", "exec")
    return result
