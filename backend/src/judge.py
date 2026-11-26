"""Phase 5 (JUDGE): The Mute Judge — zero-prose OpenRouter evaluator with strict Pydantic JSON."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.config import Settings, get_settings
from src.models import TokenDossier

logger = logging.getLogger(__name__)

JUDGE_TIMEOUT_OR_ERROR: str = "JUDGE_TIMEOUT_OR_ERROR"


class JudgeVerdict(BaseModel):
    """Strict 9-field JSON schema returned by the Mute Judge (zero free text)."""

    model_config = ConfigDict(extra="forbid")

    organic: Literal["yes", "no"]
    organic_prob: float = Field(..., ge=0.0, le=1.0)
    late: Literal["yes", "no"]
    late_prob: float = Field(..., ge=0.0, le=1.0)
    exit_risk: int = Field(..., ge=1, le=5)
    manipulation_trap: Literal["yes", "no"]
    trap_prob: float = Field(..., ge=0.0, le=1.0)
    action: Literal["buy", "watch", "pass"]
    action_prob: float = Field(..., ge=0.0, le=1.0)


JUDGE_VERDICT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "organic",
        "organic_prob",
        "late",
        "late_prob",
        "exit_risk",
        "manipulation_trap",
        "trap_prob",
        "action",
        "action_prob",
    ],
    "properties": {
        "organic": {"type": "string", "enum": ["yes", "no"]},
        "organic_prob": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "late": {"type": "string", "enum": ["yes", "no"]},
        "late_prob": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "exit_risk": {"type": "integer", "minimum": 1, "maximum": 5},
        "manipulation_trap": {"type": "string", "enum": ["yes", "no"]},
        "trap_prob": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "action": {"type": "string", "enum": ["buy", "watch", "pass"]},
        "action_prob": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}

MUTE_JUDGE_SYSTEM_PROMPT: str = (
    "You are a mute quantitative Solana memecoin risk evaluator acting like a profitable trench trader. "
    "PROHIBITED: free text, prose, advice, explanations, markdown, comments, or adjectives. "
    "Evaluate strictly the numeric/boolean fields in the JSON TokenDossier. "
    "Fields marked \"missing\" are unknown; NEVER invent or extrapolate missing values. "
    "Key quantitative heuristics: "
    "liq_to_mcap_pct < 8% increases exit_risk sharply; buys_1h << sells_1h or deep negative price_change_6h_pct signals distribution/bleeding; "
    "rugcheck_insider_flag=true or high creator_tokens_count (>5) raises manipulation_trap risk; "
    "volume_accel_5m_vs_1h >= 1.5 with positive price_change_5m_pct and buy_sell_ratio_5m >= 1.25 indicates an active rising momentum wave; "
    "whale_confirm=true with strong buy_sell_ratio_5m > 1.25 and fresh age_minutes strengthens organic momentum. "
    "Output ONLY a single valid JSON object answering these 5 closed questions:\n"
    "1. organic (\"yes\"|\"no\") + organic_prob (0.0-1.0): Are holders and buys growing organically "
    "across independent buyers with healthy buy/sell flow (5m & 1h) rather than bleeding or fake activity?\n"
    "2. late (\"yes\"|\"no\") + late_prob (0.0-1.0): Has most of the upside already happened before "
    "reaching the desk (risk of buying the top or catching a dying post-pump bleed)?\n"
    "3. exit_risk (integer 1-5): 1 = deep liquidity (good liq_to_mcap_pct >= 10%) and clean exit; "
    "5 = thin liquidity or our planned SOL entry is too large/dangerous for the pool.\n"
    "4. manipulation_trap (\"yes\"|\"no\") + trap_prob (0.0-1.0): Are there signs of wash trading, "
    "rugcheck_insider_flag=true, serial deployer tokens, mutable metadata, or abnormal concentration?\n"
    "5. action (\"buy\"|\"watch\"|\"pass\") + action_prob (0.0-1.0): \"buy\" = clean momentum + safe structure now; "
    "\"watch\" = promising but needs confirmation, recheck once in 4 minutes; \"pass\" = reject."
)

MUTE_JUDGE_AGGRESSIVE_PROMPT: str = (
    "You are a mute quantitative Solana memecoin WAVE SCALPER evaluator targeting fast +12% to +25% continuation waves. "
    "PROHIBITED: free text, prose, advice, explanations, markdown, comments, or adjectives. "
    "Evaluate strictly the numeric/boolean fields in the JSON TokenDossier. "
    "Fields marked \"missing\" are unknown; NEVER invent or extrapolate missing values. "
    "Scalper heuristics: we only need a +12% to +25% wave continuation, NOT a 10x from launch. "
    "Tokens with high real volume (volume_1h_usd >= 30000), buyers winning (buy_sell_ratio_5m >= 1.05 or buy_sell_ratio_1h >= 1.1), "
    "healthy liquidity (liq_to_mcap_pct >= 8%), clean RugCheck (rugcheck_insider_flag=false, top10_holders_pct <= 25%, dev_wallet_pct <= 4%) "
    "and active momentum (price_change_5m_pct >= 0 or volume_accel_5m_vs_1h >= 1.2) are strong action=\"buy\" scalp setups — do NOT mark them \"late\" just for being >1h old or already having strong volume. "
    "Mark late=\"yes\" ONLY if price is dumping/exhausted after a vertical blow-off top. "
    "Mark manipulation_trap=\"yes\" ONLY if rugcheck_insider_flag=true, top10_holders_pct > 25, or clear wash trading. "
    "Output ONLY a single valid JSON object answering these 5 closed questions:\n"
    "1. organic (\"yes\"|\"no\") + organic_prob (0.0-1.0): Real buyer flow and active holders?\n"
    "2. late (\"yes\"|\"no\") + late_prob (0.0-1.0): Exhausted blow-off top with no +15% wave left?\n"
    "3. exit_risk (integer 1-5): 1 = deep pool (liq_to_mcap_pct >= 10%) & tiny footprint; 5 = illiquid trap.\n"
    "4. manipulation_trap (\"yes\"|\"no\") + trap_prob (0.0-1.0): Clear rug/insider/wash trap?\n"
    "5. action (\"buy\"|\"watch\"|\"pass\") + action_prob (0.0-1.0): \"buy\" = viable +12% to +25% wave scalp now; "
    "\"watch\" = close but needs 4m confirmation; \"pass\" = skip."
)


def make_fallback_pass_verdict() -> JudgeVerdict:
    """Fail-closed verdict when OpenRouter times out, errors, or returns invalid output."""
    return JudgeVerdict(
        organic="no",
        organic_prob=0.0,
        late="yes",
        late_prob=1.0,
        exit_risk=5,
        manipulation_trap="yes",
        trap_prob=1.0,
        action="pass",
        action_prob=1.0,
    )


@dataclass(slots=True)
class JudgeResult:
    """Wrapper containing the parsed ``JudgeVerdict`` and execution telemetry."""

    verdict: JudgeVerdict
    is_fallback: bool
    error_reason: str | None
    model_used: str
    latency_ms: float
    raw_content: str | None = None


class MuteJudge:
    """Async OpenRouter client implementing the zero-prose Mute Judge."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.api_key = (
            api_key if api_key is not None else self.settings.openrouter_api_key
        ).strip()
        self.model = model or self.settings.judge_model
        self.base_url = self.settings.openrouter_base_url.rstrip("/")
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else self.settings.judge_timeout_seconds
        )
        self._owns_client = http_client is None
        self._client = http_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                headers={"Accept": "application/json"},
            )
        return self._client

    async def __aenter__(self) -> "MuteJudge":
        await self._get_client()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client if owned by this instance."""
        if self._owns_client and self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _fail_closed(
        self,
        *,
        started: float,
        reason: str = JUDGE_TIMEOUT_OR_ERROR,
        raw_content: str | None = None,
    ) -> JudgeResult:
        latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
        return JudgeResult(
            verdict=make_fallback_pass_verdict(),
            is_fallback=True,
            error_reason=reason,
            model_used=self.model,
            latency_ms=latency_ms,
            raw_content=raw_content,
        )

    @staticmethod
    def _extract_message_text(payload: Any) -> str:
        if not isinstance(payload, dict):
            raise ValueError("OpenRouter response JSON must be an object.")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("OpenRouter response missing non-empty 'choices'.")
        first = choices[0]
        if not isinstance(first, dict):
            raise ValueError("OpenRouter choice[0] must be an object.")
        message = first.get("message")
        if not isinstance(message, dict):
            raise ValueError("OpenRouter choice[0] missing 'message'.")
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            joined = "".join(parts).strip()
            if joined:
                return joined
        raise ValueError("OpenRouter message content is empty or non-string.")

    async def evaluate(self, dossier: TokenDossier) -> JudgeResult:
        """Send a strictly numerical ``TokenDossier`` to OpenRouter and parse ``JudgeVerdict``.

        Safety rule: Silence, timeout, HTTP error, or non-JSON prose NEVER implies approval;
        it always fails closed to ``action='pass'`` with ``JUDGE_TIMEOUT_OR_ERROR``.
        """
        started = time.perf_counter()

        if not self.api_key:
            logger.warning("OPENROUTER_API_KEY not configured; Judge failing closed to pass.")
            return self._fail_closed(started=started)

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://meteoro-trader.local",
            "X-Title": "METEORO TRADER SOLANA",
        }
        system_prompt = (
            MUTE_JUDGE_AGGRESSIVE_PROMPT
            if self.settings.strategy_profile == "AGRESIVO"
            else MUTE_JUDGE_SYSTEM_PROMPT
        )
        request_body: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 400,
            "reasoning": {"enabled": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "judge_verdict",
                    "strict": True,
                    "schema": JUDGE_VERDICT_JSON_SCHEMA,
                },
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": dossier.to_prompt_json()},
            ],
        }

        raw_content: str | None = None
        try:
            client = await self._get_client()
            response = await client.post(
                url,
                headers=headers,
                json=request_body,
                timeout=self.timeout_seconds,
            )
            if response.status_code >= 400:
                logger.warning(
                    "OpenRouter HTTP %s for %s: %s",
                    response.status_code,
                    dossier.symbol,
                    response.text[:200],
                )
                return self._fail_closed(started=started)

            payload = response.json()
            raw_content = self._extract_message_text(payload).strip()
            verdict = JudgeVerdict.model_validate_json(raw_content)

        except (
            httpx.TimeoutException,
            httpx.HTTPError,
            ValidationError,
            ValueError,
            KeyError,
            IndexError,
        ) as exc:
            logger.warning(
                "MuteJudge fail-closed (%s) for %s (%s): %s",
                type(exc).__name__,
                dossier.symbol,
                dossier.mint_address[:8],
                exc,
            )
            return self._fail_closed(started=started, raw_content=raw_content)

        latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
        return JudgeResult(
            verdict=verdict,
            is_fallback=False,
            error_reason=None,
            model_used=self.model,
            latency_ms=latency_ms,
            raw_content=raw_content,
        )
